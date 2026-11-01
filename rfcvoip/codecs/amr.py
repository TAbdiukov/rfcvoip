"""AMR RTP adapters using the OpenCORE and VisualOn public C interfaces.

Only RFC 4867 packet framing is implemented here. Speech coding is performed
by opencore-amr (NB encoder/decoder, WB decoder) and vo-amrwbenc (WB encoder).
"""

import ctypes
import ctypes.util
import os
from pathlib import Path
import struct
import threading
from typing import List

from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs.base import CodecAvailability, RTPCodec

_NB_BITS = (95, 103, 118, 134, 148, 159, 204, 244, 39)
_WB_BITS = (132, 177, 253, 285, 317, 365, 397, 461, 477, 40)
_LIBRARIES = {}
_AVAILABILITY = {}
_LIBRARY_LOCK = threading.Lock()
_DLL_DIRECTORIES = {}
_LIBRARY_ENV = {
    "opencore-amrnb": "RFCVOIP_AMRNB_LIBRARY",
    "opencore-amrwb": "RFCVOIP_AMRWB_LIBRARY",
    "vo-amrwbenc": "RFCVOIP_AMRWBENC_LIBRARY",
}


def _prepare_api(lib, name):
    pointer = ctypes.c_void_p
    samples = ctypes.POINTER(ctypes.c_int16)
    octets = ctypes.POINTER(ctypes.c_ubyte)
    integer = ctypes.c_int
    if name == "opencore-amrnb":
        signatures = {
            "Encoder_Interface_init": ([integer], pointer),
            "Encoder_Interface_Encode": (
                [pointer, integer, samples, octets, integer],
                integer,
            ),
            "Encoder_Interface_exit": ([pointer], None),
            "Decoder_Interface_init": ([], pointer),
            "Decoder_Interface_Decode": (
                [pointer, octets, samples, integer],
                None,
            ),
            "Decoder_Interface_exit": ([pointer], None),
        }
    elif name == "opencore-amrwb":
        signatures = {
            "D_IF_init": ([], pointer),
            "D_IF_decode": ([pointer, octets, samples, integer], None),
            "D_IF_exit": ([pointer], None),
        }
    else:
        signatures = {
            "E_IF_init": ([], pointer),
            "E_IF_encode": (
                [pointer, integer, samples, octets, integer],
                integer,
            ),
            "E_IF_exit": ([pointer], None),
        }
    for symbol, (arguments, result) in signatures.items():
        function = getattr(lib, symbol)
        function.argtypes = arguments
        function.restype = result


def _library_candidates(name):
    override = os.environ.get(_LIBRARY_ENV[name])
    if override:
        return [override]
    filenames = (
        "lib" + name + ".so.0",
        "lib" + name + ".so",
        "lib" + name + ".0.dylib",
        "lib" + name + ".dylib",
        "lib" + name + "-0.dll",
        "lib" + name + ".dll",
        name + ".dll",
    )
    candidates = []
    directory = os.environ.get("RFCVOIP_AMR_LIBRARY_DIR")
    if directory:
        candidates.extend(str(Path(directory) / item) for item in filenames)
    found = ctypes.util.find_library(name)
    if found:
        candidates.append(found)
    candidates.extend(filenames)
    # Homebrew libraries are not always on macOS's dynamic loader path.
    if os.sys.platform == "darwin":
        for directory in ("/opt/homebrew/lib", "/usr/local/lib"):
            candidates.extend(
                str(Path(directory) / item)
                for item in filenames
                if item.endswith(".dylib")
            )
    return list(dict.fromkeys(candidates))


def _load_library(name):
    if name in _LIBRARIES:
        return _LIBRARIES[name]
    last_error = "not found"
    for candidate in _library_candidates(name):
        try:
            # Windows Python 3.8+ needs an explicit dependency search path.
            directory = str(Path(candidate).resolve().parent)
            if (
                os.name == "nt"
                and Path(candidate).is_file()
                and directory not in _DLL_DIRECTORIES
            ):
                _DLL_DIRECTORIES[directory] = os.add_dll_directory(directory)
            lib = ctypes.CDLL(candidate)
            _prepare_api(lib, name)
            _LIBRARIES[name] = lib
            return lib
        except (OSError, AttributeError) as exc:
            last_error = str(exc)
    raise RuntimeError(
        f"{name} encoder/decoder library unavailable; install native AMR "
        f"libraries or set {_LIBRARY_ENV[name]}: {last_error}"
    )


def _get_libraries(wideband):
    with _LIBRARY_LOCK:
        if wideband:
            return (
                _load_library("vo-amrwbenc"),
                _load_library("opencore-amrwb"),
            )
        lib = _load_library("opencore-amrnb")
        return lib, lib


def _frame_bits(frame_type, wideband):
    sizes = _WB_BITS if wideband else _NB_BITS
    if 0 <= frame_type < len(sizes):
        return sizes[frame_type]
    if frame_type == 15 or (wideband and frame_type == 14):
        return 0
    raise ValueError("Invalid AMR frame type")


def _pack_frames(frames, octet_align, wideband):
    if octet_align:
        return (
            b"\xf0"
            + bytes(
                frame[0] | (0x80 if i + 1 < len(frames) else 0)
                for i, frame in enumerate(frames)
            )
            + b"".join(frame[1:] for frame in frames)
        )
    bits = "1111" + "".join(
        f"{int(i + 1 < len(frames)):01b}{(frame[0] >> 3) & 15:04b}"
        f"{(frame[0] >> 2) & 1:01b}"
        for i, frame in enumerate(frames)
    )
    for frame in frames:
        size = _frame_bits((frame[0] >> 3) & 15, wideband)
        bits += "".join(f"{byte:08b}" for byte in frame[1:])[:size]
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


def _unpack_frames(payload, octet_align, wideband, max_frames=50):
    """Validate the complete packet before exposing any frame to native code."""
    frames = []
    if octet_align:
        if len(payload) < 2:
            raise ValueError("Truncated AMR RTP header")
        offset = 1
        while True:
            if offset >= len(payload) or len(frames) >= max_frames:
                raise ValueError("Invalid AMR table of contents")
            toc = payload[offset]
            frames.append((toc & 0x7C, _frame_bits((toc >> 3) & 15, wideband)))
            offset += 1
            if not toc & 0x80:
                break
        result = []
        for toc, size in frames:
            count = (size + 7) // 8
            if offset + count > len(payload):
                raise ValueError("Truncated AMR speech frame")
            body = bytearray(payload[offset : offset + count])
            if size % 8:
                body[-1] &= 0xFF << (8 - size % 8)
            result.append(bytes([toc]) + body)
            offset += count
        if offset != len(payload):
            raise ValueError("Unexpected trailing AMR speech data")
        return result

    bits = "".join(f"{byte:08b}" for byte in payload)
    offset = 4
    while True:
        if offset + 6 > len(bits) or len(frames) >= max_frames:
            raise ValueError("Truncated AMR table of contents")
        toc = int(bits[offset : offset + 6], 2)
        frames.append(
            ((toc & 31) << 2, _frame_bits((toc >> 1) & 15, wideband))
        )
        offset += 6
        if not toc & 32:
            break
    result = []
    for toc, size in frames:
        if offset + size > len(bits):
            raise ValueError("Truncated AMR speech frame")
        body = bits[offset : offset + size] + "0" * (-size % 8)
        result.append(
            bytes([toc])
            + (int(body, 2).to_bytes(len(body) // 8, "big") if body else b"")
        )
        offset += size
    if len(bits) - offset > 7:
        raise ValueError("Unexpected trailing AMR speech data")
    return result


class _AMRCodec(RTPCodec):
    dynamic = True
    channels = 1
    frame_duration_ms = 20
    preferred_public_bit_depth = 16
    default_fmtp = ["octet-align=1;mode-change-capability=2"]
    wideband = False
    max_frames = 50
    requires_extra_package = True

    @classmethod
    def refresh_availability_cache(cls):
        with _LIBRARY_LOCK:
            _AVAILABILITY.pop(cls.wideband, None)
            _LIBRARIES.clear()

    @classmethod
    def availability(cls):
        cached = _AVAILABILITY.get(cls.wideband)
        if cached is not None:
            return cached
        try:
            encoder, decoder = _get_libraries(cls.wideband)
            library = ", ".join(
                dict.fromkeys(
                    str(getattr(lib, "_name", "AMR native library"))
                    for lib in (encoder, decoder)
                )
            )
            result = CodecAvailability(
                True,
                "OpenCORE/VisualOn AMR encoder and decoder available",
                library,
            )
        except (OSError, RuntimeError, AttributeError) as exc:
            result = CodecAvailability(False, str(exc))
        _AVAILABILITY[cls.wideband] = result
        return result

    @classmethod
    def fmtp_parameters(cls, settings):
        params = {}
        for setting in settings or []:
            for item in str(setting).split(";"):
                key, sep, value = item.strip().partition("=")
                key, value = key.lower().strip(), value.strip()
                if key in (
                    "octet-align",
                    "mode-set",
                    "mode-change-period",
                    "mode-change-neighbor",
                    "crc",
                    "robust-sorting",
                    "interleaving",
                    "channels",
                    "mode-change-capability",
                ):
                    if not sep or (key in params and params[key] != value):
                        raise ValueError("Malformed or conflicting AMR FMTP")
                    params[key] = value
        for key in (
            "octet-align",
            "crc",
            "robust-sorting",
            "mode-change-neighbor",
        ):
            if params.get(key, "0") not in ("0", "1"):
                raise ValueError(f"Invalid AMR {key}")
        if (
            params.get("crc", "0") != "0"
            or params.get("robust-sorting", "0") != "0"
            or "interleaving" in params
            or params.get("channels", "1") != "1"
        ):
            raise ValueError(
                "Unsupported AMR CRC, sorting, interleaving or channels"
            )
        if params.get("mode-change-period", "1") not in ("1", "2"):
            raise ValueError("Invalid AMR mode-change-period")
        if params.get("mode-change-capability", "1") not in ("1", "2"):
            raise ValueError("Invalid AMR mode-change-capability")
        modes = tuple(range(9 if cls.wideband else 8))
        if "mode-set" in params:
            tokens = params["mode-set"].split(",")
            if any(
                token.strip() not in tuple(str(m) for m in modes)
                for token in tokens
            ):
                raise ValueError("Invalid AMR mode-set")
            requested = tuple(int(token.strip()) for token in tokens)
            if len(set(requested)) != len(requested):
                raise ValueError("Duplicate AMR mode-set entry")
            modes = tuple(sorted(requested))
        return {
            "octet-align": params.get("octet-align", "0") == "1",
            "mode-set": modes,
            "mode-change-period": int(params.get("mode-change-period", "1")),
            "mode-change-neighbor": params.get("mode-change-neighbor", "0")
            == "1",
        }

    @classmethod
    def fmtp_supported(cls, fmtp: List[str]) -> bool:
        try:
            cls.fmtp_parameters(fmtp)
            return True
        except ValueError:
            return False

    @classmethod
    def negotiated_bandwidth_bps(cls, settings):
        modes = cls.fmtp_parameters(settings)["mode-set"]
        bits = _WB_BITS if cls.wideband else _NB_BITS
        return max(bits[mode] for mode in modes) * 50

    def __init__(self):
        self._encoder = None
        self._decoder = None
        self._encoder_lib, self._decoder_lib = _get_libraries(self.wideband)
        self._closed = False
        self.configure_fmtp(self.default_fmtp)

    def configure_fmtp(self, settings):
        params = self.fmtp_parameters(settings)
        self.octet_align = params["octet-align"]
        self.modes = params["mode-set"]
        self.mode = max(self.modes)
        self.mode_change_period = params["mode-change-period"]
        self.mode_change_neighbor = params["mode-change-neighbor"]
        self._requested_mode = None
        self._encoded_frames = 0

    def request_mode(self, mode):
        # CMR=15 cancels a previous request. Reserved/out-of-set values
        # cannot force a mode outside the negotiated active set.
        if mode == 15:
            self._requested_mode = None
        elif mode in self.modes:
            self._requested_mode = mode

    def _next_mode(self):
        target = (
            max(self.modes)
            if self._requested_mode is None
            else self._requested_mode
        )
        if self._encoded_frames % self.mode_change_period == 0:
            if self.mode_change_neighbor:
                current = self.modes.index(self.mode)
                desired = self.modes.index(target)
                self.mode = self.modes[
                    current + (desired > current) - (desired < current)
                ]
            else:
                self.mode = target
        self._encoded_frames += 1
        return self.mode

    def close(self):
        if self._encoder:
            function = (
                self._encoder_lib.E_IF_exit
                if self.wideband
                else self._encoder_lib.Encoder_Interface_exit
            )
            function(self._encoder)
            self._encoder = None
        if self._decoder:
            function = (
                self._decoder_lib.D_IF_exit
                if self.wideband
                else self._decoder_lib.Decoder_Interface_exit
            )
            function(self._decoder)
            self._decoder = None
        self._closed = True

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _frame_count(self, payload):
        if not payload:
            return 1
        size = self.source_frame_size()
        # Accept the rounding of fractional public sample rates, including
        # 11025 Hz, while rejecting durations which AMR cannot represent.
        count = max(1, round(len(payload) / size))
        expected = self.source_frame_size(20 * count)
        if count > self.max_frames or len(payload) != expected:
            raise ValueError("AMR audio must contain complete 20 ms frames")
        return count

    def packet_duration_seconds(self, source_payload):
        return self._frame_count(source_payload) * 0.020

    def rtp_timestamp_increment(self, source_payload, encoded_payload):
        return self._frame_count(source_payload) * (self.rate // 50)

    def encode(self, payload):
        if self._closed:
            raise RuntimeError("AMR codec is closed")
        count = self._frame_count(payload)
        samples = self.rate // 50
        pcm16 = self._source_u8_to_pcm16(payload, self.rate, target_channels=1)
        pcm16 = pcm16[: count * samples * 2].ljust(
            count * samples * 2, b"\x00"
        )
        if not self._encoder:
            self._encoder = (
                self._encoder_lib.E_IF_init()
                if self.wideband
                else self._encoder_lib.Encoder_Interface_init(0)
            )
            if not self._encoder:
                raise RuntimeError("AMR encoder initialization failed")
        frames = []
        for index in range(count):
            # Unpack little-endian public PCM into host-native C short values.
            pcm = (ctypes.c_int16 * samples)(
                *struct.unpack(
                    "<" + "h" * samples,
                    pcm16[index * samples * 2 : (index + 1) * samples * 2],
                )
            )
            output = (ctypes.c_ubyte * 64)()
            mode = self._next_mode()
            function = (
                self._encoder_lib.E_IF_encode
                if self.wideband
                else self._encoder_lib.Encoder_Interface_Encode
            )
            length = function(self._encoder, mode, pcm, output, 0)
            bits = _frame_bits(mode, self.wideband)
            if (
                length != 1 + (bits + 7) // 8
                or (output[0] >> 3) & 15 != mode
                or not output[0] & 4
            ):
                raise RuntimeError(
                    "Native AMR encoder returned an invalid speech frame"
                )
            frame = bytearray(output[:length])
            frame[0] &= 0x7C
            if bits % 8:
                frame[-1] &= 0xFF << (8 - bits % 8)
            frames.append(bytes(frame))
        return _pack_frames(frames, self.octet_align, self.wideband)

    def decode(self, payload):
        if self._closed:
            raise RuntimeError("AMR codec is closed")
        if not payload:
            return silence_bytes(
                self.source_frame_size(), self.source_bit_depth
            )
        frames = _unpack_frames(
            payload, self.octet_align, self.wideband, self.max_frames
        )
        speech_modes = 9 if self.wideband else 8
        if any(
            (frame[0] >> 3) & 15 < speech_modes
            and (frame[0] >> 3) & 15 not in self.modes
            for frame in frames
        ):
            raise ValueError(
                "AMR speech mode is outside the negotiated mode-set"
            )
        if not self._decoder:
            self._decoder = (
                self._decoder_lib.D_IF_init()
                if self.wideband
                else self._decoder_lib.Decoder_Interface_init()
            )
            if not self._decoder:
                raise RuntimeError("AMR decoder initialization failed")
        samples = self.rate // 50
        chunks = []
        for frame in frames:
            # A padded, full-size native buffer also protects native readers
            # handling erasure/no-data frames with no speech bytes.
            packet = (ctypes.c_ubyte * 64).from_buffer_copy(
                frame.ljust(64, b"\x00")
            )
            pcm = (ctypes.c_int16 * samples)()
            function = (
                self._decoder_lib.D_IF_decode
                if self.wideband
                else self._decoder_lib.Decoder_Interface_Decode
            )
            function(self._decoder, packet, pcm, int(not frame[0] & 4))
            chunks.append(struct.pack("<" + "h" * samples, *pcm))
        return self._pcm16_to_source_u8(
            b"".join(chunks), self.rate, source_channels=1
        )


class AMRCodec(_AMRCodec):
    payload_type = "AMR"
    name = "AMR"
    description = "AMR"
    rate = 8000
    preferred_source_sample_rate = 8000
    default_payload_type = 118
    priority_score = 750
    required_bandwidth_bps = 12200
    extra_packages = ("opencore-amr",)


class AMRWBCodec(_AMRCodec):
    payload_type = "AMR-WB"
    name = "AMR-WB"
    description = "AMR-WB"
    wideband = True
    rate = 16000
    preferred_source_sample_rate = 16000
    source_sample_rate = 16000
    default_payload_type = 119
    priority_score = 960
    required_bandwidth_bps = 23850
    extra_packages = ("opencore-amr", "vo-amrwbenc")
