import ctypes
from types import SimpleNamespace

import pytest

from rfcvoip.codecs import opus
from rfcvoip.codecs.g722 import G722Codec
from rfcvoip.codecs.silk import (
    Silk8000Codec,
    Silk12000Codec,
    Silk16000Codec,
    Silk24000Codec,
)


@pytest.mark.parametrize(
    ("sample_rate", "duration_ms", "native_samples"),
    [
        (11025, 20, 960),
        (11025, 10, 480),
        (11025, 40, 1920),
        (11025, 60, 2880),
        (8000, 10, 480),
        (8000, 40, 1920),
        (8000, 60, 2880),
        (48000, 5, 240),
        (48000, 1, 960),
    ],
)
def test_opus_timing_matches_native_frame_after_resampling(
    monkeypatch, sample_rate, duration_ms, native_samples
):
    frame_sizes = []

    def create_encoder(_rate, _channels, _application, error):
        ctypes.cast(error, ctypes.POINTER(ctypes.c_int))[0] = 0
        return 1

    def encode(_encoder, _pcm, size, packet, _capacity):
        frame_sizes.append(size)
        packet[0] = 1
        return 1

    backend = SimpleNamespace(
        opus_encoder_create=create_encoder,
        opus_encoder_destroy=lambda _encoder: None,
        opus_encode=encode,
    )
    monkeypatch.setattr(opus, "_get_libopus_encode_handle", lambda: backend)
    codec = opus.OpusCodec()
    codec.configure_source_format(
        sample_rate=sample_rate, bit_depth=16, channels=1
    )
    source = bytes(codec.source_frame_size(duration_ms))

    # RTPClient chooses pacing before encoding the next packet.
    period = codec.packet_duration_seconds(source)
    packet = codec.encode(source)

    assert frame_sizes == [native_samples]
    assert period == pytest.approx(native_samples / codec.rate)
    assert codec.rtp_timestamp_increment(source, packet) == native_samples


@pytest.mark.parametrize(
    "codec_cls",
    [Silk8000Codec, Silk12000Codec, Silk16000Codec, Silk24000Codec],
)
def test_silk_timing_uses_fixed_encoded_frame_duration(codec_cls):
    native_lengths = []

    def encode(source, destination, _rate, _bitrate, **_kwargs):
        native_lengths.append(len(source.read()))
        destination.write(b"#!SILK_V3\x01\x00\x55")

    codec = codec_cls.__new__(codec_cls)
    codec._pysilk = SimpleNamespace(encode=encode)
    codec.configure_source_format(sample_rate=11025, bit_depth=16, channels=1)
    source = bytes(codec.source_frame_size())

    period = codec.packet_duration_seconds(source)
    packet = codec.encode(source)

    assert native_lengths == [codec.rate // 50 * 2]
    assert period == pytest.approx(0.020)
    assert codec.rtp_timestamp_increment(source, packet) == codec.rate // 50


def test_g722_timing_matches_twenty_ms_native_frame_at_fractional_public_rate():
    native_lengths = []

    def encode(samples):
        native_lengths.append(len(samples))
        return bytes(len(samples) // 2)

    codec = G722Codec.__new__(G722Codec)
    codec._encoder = SimpleNamespace(encode=encode)
    codec.configure_source_format(sample_rate=11025, bit_depth=16, channels=2)
    source = bytes(codec.source_frame_size())

    period = codec.packet_duration_seconds(source)
    packet = codec.encode(source)

    assert native_lengths == [320]
    assert len(packet) == 160
    assert period == pytest.approx(0.020)
    assert codec.rtp_timestamp_increment(source, packet) == 160
    assert codec.rtp_timestamp_increment(source, b"") == 160


def test_g722_timestamp_increment_uses_encoded_wire_duration():
    codec = G722Codec.__new__(G722Codec)
    codec.configure_source_format(sample_rate=16000, bit_depth=16)

    assert codec.rtp_timestamp_increment(bytes(640), bytes(80)) == 80
