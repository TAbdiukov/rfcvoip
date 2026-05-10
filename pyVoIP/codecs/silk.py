from typing import List, Optional
import audioop
import importlib
import io
import struct
import threading

from pyVoIP.codecs.base import CodecAvailability, RTPCodec


_PYSILK_MODULE = None
_PYSILK_LOCK = threading.Lock()
_PYSILK_AVAILABILITY: Optional[CodecAvailability] = None

_SILK_STORAGE_HEADER = b"#!SILK_V3"
_TENCENT_SILK_STORAGE_HEADER = b"\x02#!SILK_V3"

# SILK bitrate ranges by RTP clock/sample rate.
_SILK_BITRATE_RANGE_BPS = {
    8000: (6000, 20000),
    12000: (7000, 25000),
    16000: (8000, 30000),
    24000: (12000, 40000),
}


def _get_pysilk_module():
    global _PYSILK_MODULE

    with _PYSILK_LOCK:
        if _PYSILK_MODULE is not None:
            return _PYSILK_MODULE

        module = importlib.import_module("pysilk")
        if not callable(getattr(module, "encode", None)):
            raise RuntimeError("pysilk.encode is not available")
        if not callable(getattr(module, "decode", None)):
            raise RuntimeError("pysilk.decode is not available")

        _PYSILK_MODULE = module
        return module


def _storage_payload_from_pysilk_output(data: bytes) -> bytes:
    if data.startswith(_TENCENT_SILK_STORAGE_HEADER):
        offset = len(_TENCENT_SILK_STORAGE_HEADER)
    elif data.startswith(_SILK_STORAGE_HEADER):
        offset = len(_SILK_STORAGE_HEADER)
    else:
        raise ValueError("pysilk output did not contain a SILK storage header")

    if len(data) < offset + 2:
        raise ValueError("pysilk output did not contain an encoded frame")

    frame_size = struct.unpack("<h", data[offset : offset + 2])[0]
    if frame_size < 0:
        raise ValueError("pysilk output contained a negative frame size")

    start = offset + 2
    end = start + frame_size
    frame = data[start:end]
    if len(frame) != frame_size:
        raise ValueError("pysilk output frame was truncated")

    return frame


def _pysilk_storage_input_from_rtp_payload(payload: bytes) -> bytes:
    if len(payload) > 0x7FFF:
        raise ValueError("SILK RTP payload is too large")
    return _SILK_STORAGE_HEADER + struct.pack("<h", len(payload)) + payload


def _fmtp_params(fmtp: List[str]) -> dict:
    params = {}
    text = " ".join(str(item) for item in (fmtp or []))
    for token in text.replace(";", " ").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip().strip('"')
        if key:
            params[key] = value
    return params


class SilkCodec(RTPCodec):
    """RTP SILK codec adapter backed by the optional pysilk module.

    The pysilk package exposes file-style SILK storage encode/decode helpers.
    RTP uses the raw encoded SILK frame as the packet payload, so this adapter
    converts between the two framings at the packet boundary.
    """

    name = "SILK"
    description = "SILK"
    channels = 1
    dynamic = True
    can_transmit_audio = True
    frame_duration_ms = 20
    source_sample_rate = 8000
    source_sample_width = 1
    source_channels = 1
    complexity = 2
    packet_loss_percentage = 0
    use_inband_fec = False
    use_dtx = False
    bit_rate = 20000
    default_fmtp: List[str] = []

    @classmethod
    def refresh_availability_cache(cls) -> None:
        global _PYSILK_MODULE, _PYSILK_AVAILABILITY
        with _PYSILK_LOCK:
            _PYSILK_MODULE = None
            _PYSILK_AVAILABILITY = None

    @classmethod
    def availability(cls) -> CodecAvailability:
        global _PYSILK_AVAILABILITY
        if _PYSILK_AVAILABILITY is not None:
            return _PYSILK_AVAILABILITY

        try:
            module = _get_pysilk_module()
            version = getattr(module, "__version__", None)
            library = f"pysilk {version}" if version else "pysilk"
            _PYSILK_AVAILABILITY = CodecAvailability(
                True,
                "pysilk SILK encoder/decoder available",
                library,
            )
        except Exception as exc:
            _PYSILK_AVAILABILITY = CodecAvailability(False, str(exc))
        return _PYSILK_AVAILABILITY

    @classmethod
    def rtpmap(cls, payload_type: int) -> str:
        return f"{payload_type} SILK/{cls.rate}"

    @classmethod
    def fmtp_supported(cls, fmtp: List[str]) -> bool:
        params = _fmtp_params(fmtp)
        if "usedtx" in params and params["usedtx"] not in ("0", "1"):
            return False

        max_average = params.get("maxaveragebitrate")
        if max_average is None:
            return True

        try:
            max_average_bps = int(max_average)
        except ValueError:
            return False

        if max_average_bps <= 0:
            return False

        min_bps, max_bps = _SILK_BITRATE_RANGE_BPS.get(
            cls.rate,
            (0, cls.bit_rate),
        )
        if max_average_bps < min_bps:
            return False

        # PyVoIP's current adapter does not carry per-peer fmtp into the codec
        # instance, so reject caps lower than the encoder setting we will use.
        return max_average_bps >= min(cls.bit_rate, max_bps)

    def __init__(self):
        self._pysilk = _get_pysilk_module()
        self._encode_rate_state = None
        self._decode_rate_state = None

    @property
    def _samples_per_frame(self) -> int:
        return int((self.rate * self.frame_duration_ms) / 1000)

    @property
    def _pcm16_bytes_per_frame(self) -> int:
        return self._samples_per_frame * 2

    @staticmethod
    def _fit(data: bytes, length: int, pad: bytes) -> bytes:
        if len(data) >= length:
            return data[:length]
        return data + (pad * (length - len(data)))

    def _public_to_silk_pcm16(self, payload: bytes) -> bytes:
        pcm16 = self._source_u8_to_pcm16(payload, self.rate)
        return self._fit(pcm16, self._pcm16_bytes_per_frame, b"\x00")

    def _silk_pcm16_to_public(self, pcm16: bytes) -> bytes:
        public = self._pcm16_to_source_u8(pcm16, self.rate)
        return self._fit(
            public,
            self.source_frame_size(self.frame_duration_ms),
            b"\x80",
        )

    def encode(self, payload: bytes) -> bytes:
        pcm16 = self._public_to_silk_pcm16(payload)
        source = io.BytesIO(pcm16)
        encoded = io.BytesIO()

        self._pysilk.encode(
            source,
            encoded,
            self.rate,
            self.bit_rate,
            max_internal_sample_rate=self.rate,
            packet_loss_percentage=self.packet_loss_percentage,
            complexity=self.complexity,
            use_inband_fec=self.use_inband_fec,
            use_dtx=self.use_dtx,
            tencent=False,
        )
        return _storage_payload_from_pysilk_output(encoded.getvalue())

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return b"\x80" * self.source_frame_size()

        source = io.BytesIO(_pysilk_storage_input_from_rtp_payload(payload))
        decoded = io.BytesIO()
        self._pysilk.decode(
            source,
            decoded,
            self.rate,
            frame_size=self._samples_per_frame,
            frames_per_packet=1,
        )
        return self._silk_pcm16_to_public(decoded.getvalue())


class Silk24000Codec(SilkCodec):
    payload_type = "SILK/24000"
    rate = 24000
    preferred_source_sample_rate = 24000
    source_sample_rate = 24000
    default_payload_type = 114
    priority_score = 950
    bit_rate = 24000


class Silk16000Codec(SilkCodec):
    payload_type = "SILK/16000"
    rate = 16000
    preferred_source_sample_rate = 16000
    source_sample_rate = 16000
    default_payload_type = 115
    priority_score = 940
    bit_rate = 20000


class Silk12000Codec(SilkCodec):
    payload_type = "SILK/12000"
    rate = 12000
    preferred_source_sample_rate = 12000
    source_sample_rate = 12000
    default_payload_type = 116
    priority_score = 930
    bit_rate = 16000


class Silk8000Codec(SilkCodec):
    payload_type = "SILK/8000"
    rate = 8000
    preferred_source_sample_rate = 8000
    source_sample_rate = 8000
    default_payload_type = 117
    priority_score = 920
    bit_rate = 12000