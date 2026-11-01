import pytest

from rfcvoip import RTP
from rfcvoip.audio_format import PublicAudioFormat


@pytest.mark.parametrize("duration_ms", [2.5, 1.25, 0.25])
def test_public_audio_format_preserves_fractional_frame_duration(duration_ms):
    audio_format = PublicAudioFormat(48000, 2, 16, frame_ms=duration_ms)

    assert audio_format.frame_ms == duration_ms
    assert audio_format.frame_size == round(48000 * duration_ms / 1000) * 4
    assert audio_format.as_dict()["frame_ms"] == duration_ms


def test_rtp_frame_size_matches_reported_two_point_five_ms_public_format():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40000,
        RTP.TransmitType.SENDRECV,
        audio_sample_rate=48000,
        audio_channels=2,
        audio_bit_depth=16,
    )

    assert client.audio_format(2.5)["frame_ms"] == 2.5
    assert client.audio_frame_size(2.5) == 480
    assert client.audio_format(2.5)["frame_size"] == 480


@pytest.mark.parametrize("duration_ms", [float("inf"), float("-inf")])
def test_public_audio_format_rejects_nonfinite_duration(duration_ms):
    with pytest.raises(ValueError, match="duration"):
        PublicAudioFormat(48000, 2, 16, frame_ms=duration_ms)
