from pyVoIP.RTP import PayloadType, RTPClient, TransmitType
from pyVoIP.VoIP.VoIP import VoIPCall


def test_rtp_client_exposes_selected_codec_telemetry():
    client = RTPClient(
        {0: PayloadType.PCMU, 101: PayloadType.EVENT},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        TransmitType.SENDRECV,
    )

    telemetry = client.selected_codec_info()

    assert telemetry["name"] == "PCMU"
    assert telemetry["payload_type"] == 0
    assert telemetry["source"] == "active-call"
    assert telemetry["rtp"]["local"] == {"ip": "127.0.0.1", "port": 10000}
    assert telemetry["rtp"]["remote"] == {"ip": "127.0.0.1", "port": 10002}
    assert telemetry["rtp"]["transmit_type"] == "sendrecv"
    assert telemetry["public_audio_format"] == {
        "sample_rate": 8000,
        "sample_width": 1,
        "channels": 1,
        "encoding": "unsigned-8bit-linear",
    }


def test_voip_call_codec_report_includes_active_codecs():
    class FakeRTPClient:
        def selected_codec_info(self):
            return {"name": "PCMU", "payload_type": 0}

    call = object.__new__(VoIPCall)
    call.remote_sip_message = None
    call.RTPClients = [FakeRTPClient()]

    assert call.active_codecs() == [{"name": "PCMU", "payload_type": 0}]
    assert call.codec_support_report()["active_codecs"] == [
        {"name": "PCMU", "payload_type": 0}
    ]