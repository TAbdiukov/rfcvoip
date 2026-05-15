from typing import List, Optional
import audioop
import ctypes
import ctypes.util
import threading

from rfcvoip.codecs.base import CodecAvailability, RTPCodec


OPUS_APPLICATION_VOIP = 2048

_LIBOPUS_ENCODE_HANDLE = None
_LIBOPUS_ENCODE_LOCK = threading.Lock()
_LIBOPUS_AVAILABILITY: Optional[CodecAvailability] = None


def _prepare_libopus_encoder_api(lib) -> None:
    lib.opus_encoder_create.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.opus_encoder_create.restype = ctypes.c_void_p

    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_encoder_destroy.restype = None

    lib.opus_encode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
    ]
    lib.opus_encode.restype = ctypes.c_int

    lib.opus_decoder_create.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.opus_decoder_create.restype = ctypes.c_void_p

    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_destroy.restype = None

    lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.opus_decode.restype = ctypes.c_int

    lib.opus_strerror.argtypes = [ctypes.c_int]
    lib.opus_strerror.restype = ctypes.c_char_p


def _opus_error_message(lib, code: int) -> str:
    try:
        raw = lib.opus_strerror(int(code))
        if raw:
            return raw.decode("utf-8", errors="replace")
    except Exception:
        pass
    return f"libopus error {code}"


def _dsopus_loaded_opus_library():
    try:
        import discord  # type: ignore

        opus_module = getattr(discord, "opus", None)
        if opus_module is None:
            return None

        is_loaded = getattr(opus_module, "is_loaded", None)
        if callable(is_loaded) and not is_loaded():
            return None

        dsopus_lib = getattr(opus_module, "_lib", None)
        if dsopus_lib is None:
            return None

        raw_name = getattr(dsopus_lib, "_name", None)
        return ctypes.CDLL(raw_name) if raw_name else dsopus_lib
    except Exception:
        return None


def _get_libopus_encode_handle():
    global _LIBOPUS_ENCODE_HANDLE

    with _LIBOPUS_ENCODE_LOCK:
        if _LIBOPUS_ENCODE_HANDLE is not None:
            return _LIBOPUS_ENCODE_HANDLE

        try:
            lib = _dsopus_loaded_opus_library()
            if lib is not None:
                _prepare_libopus_encoder_api(lib)
                _LIBOPUS_ENCODE_HANDLE = lib
                return lib
        except Exception:
            pass

        candidates: List[str] = []
        try:
            found = ctypes.util.find_library("opus")
            if found:
                candidates.append(found)
        except Exception:
            pass

        for name in (
            "libopus.so.0",
            "libopus.so.1",
            "libopus.dylib",
            "opus.dll",
            "libopus-0.x64.dll",
            "libopus-0.dll",
        ):
            if name not in candidates:
                candidates.append(name)

        last_error = None
        for name in candidates:
            try:
                lib = ctypes.CDLL(name)
                _prepare_libopus_encoder_api(lib)
                _LIBOPUS_ENCODE_HANDLE = lib
                return lib
            except Exception as exc:
                last_error = exc

        raise RuntimeError(
            f"libopus encoder unavailable: {last_error or 'not found'}"
        )


class OpusCodec(RTPCodec):
    payload_type = "opus"
    name = "opus"
    description = "opus"
    rate = 48000
    channels = 1
    rtpmap_channels = 2
    default_payload_type = 111
    dynamic = True
    priority_score = 1000
    preferred_source_sample_rate = 48000
    source_sample_rate = 48000
    default_fmtp = ["minptime=10;useinbandfec=1"]
    max_data_bytes = 4000
    max_frame_size = 5760

    @classmethod
    def refresh_availability_cache(cls) -> None:
        global _LIBOPUS_AVAILABILITY
        _LIBOPUS_AVAILABILITY = None

    @classmethod
    def availability(cls) -> CodecAvailability:
        global _LIBOPUS_AVAILABILITY
        if _LIBOPUS_AVAILABILITY is not None:
            return _LIBOPUS_AVAILABILITY

        try:
            lib = _get_libopus_encode_handle()
            library = getattr(lib, "_name", None) or "discord.opus._lib"
            _LIBOPUS_AVAILABILITY = CodecAvailability(
                True,
                "libopus encoder/decoder available",
                str(library),
            )
        except Exception as exc:
            _LIBOPUS_AVAILABILITY = CodecAvailability(False, str(exc))
        return _LIBOPUS_AVAILABILITY

    @classmethod
    def rtpmap(cls, payload_type: int) -> str:
        # RFC-style Opus SDP commonly advertises opus/48000/2. rfcvoip still
        # exposes mono 8 kHz audio to user code and downmixes internally.
        return f"{payload_type} opus/48000/2"

    def __init__(self):
        self._lib = _get_libopus_encode_handle()
        self._encoder = None
        self._decoder = None
        self._encode_rate_state = None
        self._decode_rate_state = None

    def __del__(self):
        try:
            if self._encoder:
                self._lib.opus_encoder_destroy(self._encoder)
                self._encoder = None
            if self._decoder:
                self._lib.opus_decoder_destroy(self._decoder)
                self._decoder = None
        except Exception:
            pass

    def _ensure_encoder(self):
        if self._encoder:
            return self._encoder

        err = ctypes.c_int()
        encoder = self._lib.opus_encoder_create(
            self.rate,
            self.channels,
            OPUS_APPLICATION_VOIP,
            ctypes.byref(err),
        )
        if err.value != 0 or not encoder:
            raise RuntimeError(_opus_error_message(self._lib, err.value))
        self._encoder = encoder
        return encoder

    def _ensure_decoder(self):
        if self._decoder:
            return self._decoder

        err = ctypes.c_int()
        decoder = self._lib.opus_decoder_create(
            self.rate,
            self.channels,
            ctypes.byref(err),
        )
        if err.value != 0 or not decoder:
            raise RuntimeError(_opus_error_message(self._lib, err.value))
        self._decoder = decoder
        return decoder

    @staticmethod
    def _valid_frame_size(frame_size: int) -> bool:
        return frame_size in {120, 240, 480, 960, 1920, 2880}

    def _to_opus_pcm(self, payload: bytes) -> bytes:
        pcm16_48k = self._source_u8_to_pcm16(payload, self.rate)

        frame_size = len(pcm16_48k) // 2
        if self._valid_frame_size(frame_size):
            return pcm16_48k

        # The transmitter normally feeds 160 bytes = 20 ms @ 8 kHz. Keep a
        # valid Opus frame even if a caller passes an unusual size.
        target_frame_size = 960
        target_bytes = target_frame_size * 2
        return pcm16_48k[:target_bytes].ljust(target_bytes, b"\x00")

    def encode(self, payload: bytes) -> bytes:
        if not payload:
            payload = b"\x80" * self.source_frame_size()

        pcm16_48k = self._to_opus_pcm(payload)
        frame_size = len(pcm16_48k) // 2
        pcm = (ctypes.c_int16 * frame_size).from_buffer_copy(pcm16_48k)
        encoded = (ctypes.c_ubyte * self.max_data_bytes)()

        encoded_len = self._lib.opus_encode(
            self._ensure_encoder(),
            pcm,
            frame_size,
            encoded,
            self.max_data_bytes,
        )
        if encoded_len < 0:
            raise RuntimeError(_opus_error_message(self._lib, encoded_len))
        return bytes(encoded[:encoded_len])

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return b"\x80" * self.source_frame_size()

        packet = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        pcm = (ctypes.c_int16 * (self.max_frame_size * self.channels))()
        decoded_samples = self._lib.opus_decode(
            self._ensure_decoder(),
            packet,
            len(payload),
            pcm,
            self.max_frame_size,
            0,
        )
        if decoded_samples < 0:
            raise RuntimeError(_opus_error_message(self._lib, decoded_samples))

        pcm16_48k = ctypes.string_at(
            ctypes.addressof(pcm),
            decoded_samples * self.channels * 2,
        )
        if self.channels > 1:
            pcm16_48k = audioop.tomono(pcm16_48k, 2, 0.5, 0.5)

        return self._pcm16_to_source_u8(pcm16_48k, self.rate)
