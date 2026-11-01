from types import SimpleNamespace

from rfcvoip import RTP, Telemetry


def test_active_codec_telemetry_preserves_selected_payload_fmtp():
    client = SimpleNamespace(
        preference=RTP.PayloadType.OPUS,
        preference_payload_type=111,
        codec_fmtp={111: ["stereo=1"], 112: ["mode-set=1"]},
        inIP="127.0.0.1",
        inPort=30000,
        outIP="127.0.0.1",
        outPort=40000,
        sendrecv=RTP.TransmitType.SENDRECV,
        audio_sample_rate=48000,
        audio_channels=2,
        audio_bit_depth=16,
    )

    info = Telemetry.rtp_client_codec_info(client)

    assert info["fmtp"] == ["stereo=1"]
    assert info["rtp_options"][0]["fmtp"] == ["stereo=1"]
    assert info["public_audio"]["frame_size"] == 3840
