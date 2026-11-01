import pytest

from rfcvoip import RTP
from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs import create_codec


@pytest.mark.parametrize(
    "payload_type", [RTP.PayloadType.PCMU, RTP.PayloadType.PCMA]
)
def test_g711_timestamp_increment_follows_actual_resampled_wire_samples(
    payload_type,
):
    codec = create_codec(
        payload_type, source_sample_rate=11025, source_bit_depth=16
    )
    source = silence_bytes(codec.source_frame_size(), 16)
    increments = []
    encoded_lengths = []

    for _ in range(5):
        packet = codec.encode(source)
        encoded_lengths.append(len(packet))
        increments.append(codec.rtp_timestamp_increment(source, packet))

    assert encoded_lengths[0] == 159
    assert increments == encoded_lengths


@pytest.mark.parametrize(
    "payload_type", [RTP.PayloadType.PCMU_WB, RTP.PayloadType.PCMA_WB]
)
@pytest.mark.parametrize("sample_rate", [8000, 11025, 22050])
@pytest.mark.parametrize("duration_ms", [5, 20, 60])
def test_g711_wideband_timing_follows_complete_core_frames(
    payload_type, sample_rate, duration_ms
):
    codec = create_codec(
        payload_type, source_sample_rate=sample_rate, source_bit_depth=16
    )
    source = silence_bytes(codec.source_frame_size(duration_ms), 16)

    period = codec.packet_duration_seconds(source)
    packet = codec.encode(source)

    assert len(packet) == 1 + duration_ms * 8
    assert period == pytest.approx(duration_ms / 1000)
    assert codec.rtp_timestamp_increment(source, packet) == duration_ms * 16


def test_g711_wideband_short_source_uses_the_padded_five_ms_wire_duration():
    codec = create_codec(
        RTP.PayloadType.PCMU_WB,
        source_sample_rate=8000,
        source_bit_depth=16,
    )
    source = bytes(20)  # Ten 8 kHz public samples, padded to one core frame.

    packet = codec.encode(source)

    assert len(packet) == 41
    assert codec.packet_duration_seconds(source) == pytest.approx(0.005)
    assert codec.rtp_timestamp_increment(source, packet) == 80
