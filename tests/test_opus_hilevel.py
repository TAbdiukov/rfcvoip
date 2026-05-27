import ctypes
import struct

import pytest

from rfcvoip.RTP import PayloadType
from rfcvoip.codecs import create_codec
from rfcvoip.codecs import opus as opus_module
from rfcvoip.codecs.opus import OPUS_APPLICATION_VOIP, OpusCodec


class FakeLibopus:
    """Small ctypes-compatible libopus double for high-level codec tests."""

    _name = "fake-libopus"

    def __init__(self):
        self.encoder_create_calls = []
        self.decoder_create_calls = []
        self.encoder_destroy_calls = []
        self.decoder_destroy_calls = []
        self.encoded_frame_sizes = []
        self.encoded_sample_counts = []
        self.decoded_payloads = []
        self.decode_samples = 3

    @staticmethod
    def _set_error(error_pointer, value=0):
        ctypes.cast(error_pointer, ctypes.POINTER(ctypes.c_int))[0] = value

    def opus_encoder_create(self, rate, channels, application, error_pointer):
        self._set_error(error_pointer)
        self.encoder_create_calls.append((rate, channels, application))
        return ctypes.c_void_p(1001)

    def opus_encoder_destroy(self, encoder):
        self.encoder_destroy_calls.append(encoder)

    def opus_decoder_create(self, rate, channels, error_pointer):
        self._set_error(error_pointer)
        self.decoder_create_calls.append((rate, channels))
        return ctypes.c_void_p(2001)

    def opus_decoder_destroy(self, decoder):
        self.decoder_destroy_calls.append(decoder)

    def opus_encode(
        self,
        encoder,
        pcm,
        frame_size,
        encoded,
        max_data_bytes,
    ):
        payload = b"fake-opus-packet"
        assert len(payload) <= max_data_bytes

        self.encoded_frame_sizes.append(frame_size)
        self.encoded_sample_counts.append(frame_size * OpusCodec.channels)

        for index, byte in enumerate(payload):
            encoded[index] = byte
        return len(payload)

    def opus_decode(
        self,
        decoder,
        packet,
        packet_length,
        pcm,
        max_frame_size,
        decode_fec,
    ):
        self.decoded_payloads.append(bytes(packet[:packet_length]))
        samples = min(self.decode_samples, max_frame_size)

        for index in range(samples * OpusCodec.channels):
            pcm[index] = 100 + index
        return samples

    def opus_strerror(self, code):
        return f"fake libopus error {code}".encode("utf-8")


@pytest.fixture(autouse=True)
def clear_opus_cache(monkeypatch):
    monkeypatch.setattr(opus_module, "_LIBOPUS_AVAILABILITY", None)
    monkeypatch.setattr(opus_module, "_LIBOPUS_ENCODE_HANDLE", None)


@pytest.fixture
def fake_libopus(monkeypatch):
    fake = FakeLibopus()
    monkeypatch.setattr(opus_module, "_get_libopus_encode_handle", lambda: fake)
    return fake


def test_opus_sdp_mapping_advertises_48khz_stereo():
    assert OpusCodec.rtpmap(111) == "111 opus/48000/2"


def test_availability_reports_loaded_libopus(fake_libopus):
    availability = OpusCodec.availability()

    assert availability.available is True
    assert availability.reason == "libopus encoder/decoder available"
    assert availability.library == "fake-libopus"


def test_availability_reports_missing_libopus(monkeypatch):
    def missing_libopus():
        raise RuntimeError("libopus is not installed")

    monkeypatch.setattr(opus_module, "_get_libopus_encode_handle", missing_libopus)

    availability = OpusCodec.availability()

    assert availability.available is False
    assert "libopus is not installed" in availability.reason


def test_registered_opus_codec_uses_configured_public_audio_format(fake_libopus):
    codec = create_codec(
        PayloadType.OPUS,
        source_sample_rate=48000,
        source_bit_depth=16,
        source_channels=2,
    )

    assert isinstance(codec, OpusCodec)
    assert codec.source_sample_rate == 48000
    assert codec.source_bit_depth == 16
    assert codec.source_sample_width == 2
    assert codec.source_channels == 2
    assert codec.source_frame_size() == 3840


def test_encode_returns_libopus_packet_and_uses_valid_opus_frame(fake_libopus):
    codec = OpusCodec()
    codec.configure_source_format(
        sample_rate=48000,
        bit_depth=16,
        channels=2,
    )

    encoded = codec.encode(b"\x01\x00" * 20)

    assert encoded == b"fake-opus-packet"
    assert fake_libopus.encoder_create_calls == [
        (48000, 2, OPUS_APPLICATION_VOIP)
    ]
    assert fake_libopus.encoded_frame_sizes == [960]
    assert fake_libopus.encoded_sample_counts == [1920]


def test_decode_returns_configured_public_pcm(fake_libopus):
    codec = OpusCodec()
    codec.configure_source_format(
        sample_rate=48000,
        bit_depth=16,
        channels=2,
    )

    decoded = codec.decode(b"\xde\xad\xbe\xef")

    expected_samples = [100, 101, 102, 103, 104, 105]
    expected_pcm = struct.pack("<" + "h" * len(expected_samples), *expected_samples)
    assert decoded == expected_pcm
    assert fake_libopus.decoder_create_calls == [(48000, 2)]
    assert fake_libopus.decoded_payloads == [b"\xde\xad\xbe\xef"]


def test_decode_empty_payload_returns_silence_without_decoder(fake_libopus):
    codec = OpusCodec()
    codec.configure_source_format(
        sample_rate=48000,
        bit_depth=16,
        channels=2,
    )

    decoded = codec.decode(b"")

    assert decoded == b"\x00" * codec.source_frame_size()
    assert fake_libopus.decoder_create_calls == []