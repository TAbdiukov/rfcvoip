import importlib
import importlib.metadata
import struct
import threading
from typing import Optional, Sequence

from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs.base import CodecAvailability, RTPCodec


_G722_CLASS = None
_G722_LOCK = threading.Lock()
_G722_AVAILABILITY: Optional[CodecAvailability] = None
_MIN_G722_VERSION = (1, 2, 8)
_MIN_G722_VERSION_TEXT = "1.2.8"


def _g722_version_tuple(version: object):
    parts = []
    for part in str(version or "").split("."):
        digits = []
        for char in part:
            if not char.isdigit():
                break
            digits.append(char)
        parts.append(int("".join(digits)) if digits else 0)
        if len(parts) == 3:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _installed_g722_version(module) -> str:
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        return importlib.metadata.version("G722")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _ensure_supported_g722_version(module) -> None:
    version = _installed_g722_version(module)
    if _g722_version_tuple(version) < _MIN_G722_VERSION:
        raise RuntimeError(
            "G722 package version "
            + f"{_MIN_G722_VERSION_TEXT} or newer is required; "
            + f"found {version or 'unknown'}."
        )


def _get_g722_class():
    global _G722_CLASS

    with _G722_LOCK:
        if _G722_CLASS is not None:
            return _G722_CLASS

        importlib.invalidate_caches()
        try:
            module = importlib.import_module("G722")
        except Exception as ex:
            raise RuntimeError(
                "G722>=1.2.8 package is not available; install "
                + "rfcvoip[g722] or install G722>=1.2.8 separately."
            ) from ex

        _ensure_supported_g722_version(module)
        cls = getattr(module, "G722", None)
        if not callable(cls):
            raise RuntimeError("G722.G722 class is not available.")

        _G722_CLASS = cls
        return cls


def _new_g722(sample_rate: int, bit_rate: int):
    cls = _get_g722_class()
    try:
        # Newer G722 releases can avoid NumPy output explicitly.  Older
        # releases only accept (sample_rate, bit_rate), so keep compatibility.
        return cls(sample_rate, bit_rate, use_numpy=False)
    except TypeError:
        return cls(sample_rate, bit_rate)


def _pcm16le_to_samples(pcm16: bytes) -> Sequence[int]:
    sample_count = len(pcm16) // 2
    if sample_count <= 0:
        return ()

    pcm16 = pcm16[: sample_count * 2]
    return struct.unpack("<" + ("h" * sample_count), pcm16)


def _clamp_int16(value) -> int:
    try:
        value = int(value)
    except Exception:
        value = 0
    return max(-32768, min(32767, value))


def _samples_to_pcm16le(samples) -> bytes:
    values = [_clamp_int16(sample) for sample in samples]
    if not values:
        return b""
    return struct.pack("<" + ("h" * len(values)), *values)


def _pad_pcm16_to_even_sample_count(pcm16: bytes) -> bytes:
    if len(pcm16) % 2:
        pcm16 = pcm16[:-1]

    sample_count = len(pcm16) // 2
    if sample_count % 2:
        pcm16 += b"\x00\x00"
    return pcm16


class G722Codec(RTPCodec):
    """G.722 RTP codec adapter backed by the optional G722 package.

    RFC 3551 keeps static RTP payload 9 on an 8 kHz timestamp clock for
    backwards compatibility, even though G.722 encodes 16 kHz audio.  The
    inherited ``rate`` therefore remains 8000 for RTP/SDP and timestamp math,
    while ``codec_sample_rate`` is 16000 for the actual encoder/decoder.
    """

    payload_type = "G722"
    name = "G722"
    description = "G722"
    rate = 8000
    channels = 1
    default_payload_type = 9
    can_transmit_audio = True
    priority_score = 910
    frame_duration_ms = 20
    preferred_public_bit_depth = 16
    preferred_source_sample_rate = 16000
    source_sample_rate = 16000
    bit_rate = 64000
    codec_sample_rate = 16000
    required_bandwidth_bps = 64000
    extra_packages = ("G722",)
    package_extras = ("g722",)
    requires_extra_package = True
    requires_compiler = False

    @classmethod
    def refresh_availability_cache(cls) -> None:
        global _G722_CLASS, _G722_AVAILABILITY
        with _G722_LOCK:
            _G722_CLASS = None
            _G722_AVAILABILITY = None

    @classmethod
    def availability(cls) -> CodecAvailability:
        global _G722_AVAILABILITY
        if _G722_AVAILABILITY is not None:
            return _G722_AVAILABILITY

        try:
            _new_g722(cls.codec_sample_rate, cls.bit_rate)
            module = importlib.import_module("G722")
            version = _installed_g722_version(module)
            library = f"G722 {version}" if version else "G722"
            _G722_AVAILABILITY = CodecAvailability(
                True,
                "G722 encoder/decoder available",
                library,
            )
        except Exception as exc:
            _G722_AVAILABILITY = CodecAvailability(False, str(exc))
        return _G722_AVAILABILITY

    def __init__(self):
        self._encoder = _new_g722(self.codec_sample_rate, self.bit_rate)
        self._decoder = _new_g722(self.codec_sample_rate, self.bit_rate)
        self._encode_rate_state = None
        self._decode_rate_state = None

    def source_frame_size(self, duration_ms: Optional[int] = None) -> int:
        return super().source_frame_size(duration_ms)

    def encode(self, payload: bytes) -> bytes:
        expected = self.source_frame_size()
        pad = b"\x80" if self.source_bit_depth == 8 else b"\x00"
        payload = self._fit_bytes(payload or b"", expected, pad)

        pcm16_16k = self._source_u8_to_pcm16(payload, self.codec_sample_rate)
        pcm16_16k = _pad_pcm16_to_even_sample_count(pcm16_16k)

        samples = _pcm16le_to_samples(pcm16_16k)
        return bytes(self._encoder.encode(samples))

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return silence_bytes(
                self.source_frame_size(),
                self.source_bit_depth,
            )

        decoded_samples = self._decoder.decode(payload)
        pcm16_16k = _samples_to_pcm16le(decoded_samples)
        public = self._pcm16_to_source_u8(pcm16_16k, self.codec_sample_rate)

        # 64 kbps G.722: 160 payload bytes = 20 ms.
        expected_length = int(
            round(
                (len(payload) / 8000.0)
                * self.source_sample_rate
                * self.source_sample_width
                * self.source_channels
            )
        )
        pad = b"\x80" if self.source_bit_depth == 8 else b"\x00"
        return self._fit_bytes(public, expected_length, pad)