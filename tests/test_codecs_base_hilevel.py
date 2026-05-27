import struct

import pytest

from rfcvoip.codecs.base import CodecAvailability, RTPCodec


class CodecUnderTest(RTPCodec):
    rate = 8000
    channels = 1
    frame_duration_ms = 20
    preferred_source_sample_rate = 8000

    def encode(self, payload: bytes) -> bytes:
        return self._source_u8_to_pcm16(payload, self.rate)

    def decode(self, payload: bytes) -> bytes:
        return self._pcm16_to_source_u8(payload, self.rate)


def test_availability_as_dict_is_stable_and_serialisable():
    availability = CodecAvailability(
        available=True,
        reason="built in",
        library="test-lib",
    )

    assert availability.as_dict() == {
        "available": True,
        "reason": "built in",
        "library": "test-lib",
    }


def test_source_format_configuration_derives_frame_sizes_and_resets_state():
    codec = CodecUnderTest()
    codec._encode_rate_state = object()
    codec._decode_rate_state = object()

    codec.configure_source_format(
        sample_rate="16000",
        sample_width=2,
        channels="2",
    )

    assert codec.source_sample_rate == 16000
    assert codec.source_bit_depth == 16
    assert codec.source_sample_width == 2
    assert codec.source_channels == 2
    assert codec._encode_rate_state is None
    assert codec._decode_rate_state is None
    assert codec.source_frame_size() == 1280
    assert codec.source_frame_size(10) == 640


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sample_rate": 0}, "sample rate"),
        ({"channels": 3}, "mono or stereo"),
        ({"bit_depth": "best"}, "resolved before use"),
        ({"bit_depth": 12}, "audio_bit_depth"),
    ],
)
def test_source_format_configuration_rejects_unsupported_audio_formats(
    overrides,
    message,
):
    codec = CodecUnderTest()
    kwargs = {
        "sample_rate": 8000,
        "sample_width": 1,
        "channels": 1,
        "bit_depth": 8,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match=message):
        codec.configure_source_format(**kwargs)


def test_public_audio_round_trips_through_codec_conversion_helpers():
    codec = CodecUnderTest()
    codec.configure_source_format(
        sample_rate=8000,
        sample_width=1,
        channels=1,
        bit_depth=8,
    )

    public_audio = b"\x00\x80\xff"
    encoded = codec.encode(public_audio)

    assert struct.unpack("<hhh", encoded) == (-32768, 0, 32512)
    assert codec.decode(encoded) == public_audio


def test_empty_payloads_use_format_appropriate_silence():
    codec = CodecUnderTest()
    codec.configure_source_format(
        sample_rate=8000,
        sample_width=1,
        channels=1,
        bit_depth=8,
    )

    assert codec.encode(b"") == b"\x00\x00" * 160

    codec.configure_source_format(
        sample_rate=8000,
        sample_width=2,
        channels=1,
        bit_depth=16,
    )

    assert codec.decode(b"") == b"\x00" * 320


def test_conversion_respects_configured_channel_count():
    codec = CodecUnderTest()
    codec.configure_source_format(
        sample_rate=8000,
        sample_width=2,
        channels=2,
        bit_depth=16,
    )

    stereo_silence = b"\x00\x00" * 320

    assert len(stereo_silence) == codec.source_frame_size()

    mono_pcm16 = codec.encode(stereo_silence)
    restored = codec.decode(mono_pcm16)

    assert len(mono_pcm16) == 320
    assert restored == stereo_silence


def test_timing_helpers_use_public_format_and_rtp_clock():
    codec = CodecUnderTest()
    codec.configure_source_format(
        sample_rate=16000,
        sample_width=2,
        channels=1,
        bit_depth=16,
    )

    public_frame = b"\x00" * codec.source_frame_size()

    assert codec.packet_duration_seconds(public_frame) == pytest.approx(0.02)
    assert codec.rtp_timestamp_increment(public_frame, b"encoded") == 160
    assert codec.output_offset(80) == 320
    assert codec.output_offset(160) == 640