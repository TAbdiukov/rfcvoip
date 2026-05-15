from pyVoIP.RTP import PayloadType, RTPClient, TransmitType
from pyVoIP.VoIP.VoIP import VoIPCall


def test_rtp_client_exposes_selected_codec_telemetry():
    from pyVoIP import Telemetry

    client = RTPClient(
        {0: PayloadType.PCMU, 101: PayloadType.EVENT},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        TransmitType.SENDRECV,
    )

    telemetry = Telemetry.rtp_client_codec_info(client)

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
    from pyVoIP import Telemetry

    class FakeRTPClient:
        def selected_codec_info(self):
            return {"name": "PCMU", "payload_type": 0}

    call = object.__new__(VoIPCall)
    call.remote_sip_message = None
    call.RTPClients = [FakeRTPClient()]

    assert Telemetry.call_active_codecs(call) == [{"name": "PCMU", "payload_type": 0}]
    assert Telemetry.call_codec_report(call)["active_codecs"] == [
        {"name": "PCMU", "payload_type": 0}
    ]