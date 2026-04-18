from types import SimpleNamespace

import pyVoIP.RTP as RTP
from pyVoIP.VoIP.VoIP import VoIPCall


class DummyRTPClient:
    def __init__(
        self,
        assoc,
        inIP,
        inPort,
        outIP,
        outPort,
        sendrecv,
        dtmf=None,
    ):
        self.assoc = assoc
        self.inIP = inIP
        self.inPort = inPort
        self.outIP = outIP
        self.outPort = outPort
        self.sendrecv = sendrecv
        self.dtmf = dtmf


def make_call():
    call = VoIPCall.__new__(VoIPCall)
    call.call_id = "call-1"
    call.RTPClients = []
    call.sendmode = "sendrecv"
    call.dtmf_callback = lambda code: None
    return call


def make_request(*addresses):
    return SimpleNamespace(
        body={
            "c": [{"address": address} for address in addresses],
        }
    )


def test_create_rtp_clients_uses_one_local_socket_for_multi_connection_sdp(
    monkeypatch,
):
    monkeypatch.setattr(RTP, "RTPClient", DummyRTPClient)
    call = make_call()
    codecs = {0: RTP.PayloadType.PCMU}

    call.create_rtp_clients(
        codecs,
        "192.0.2.10",
        10000,
        make_request("198.51.100.10", "198.51.100.11"),
        20000,
    )

    assert len(call.RTPClients) == 1
    assert call.RTPClients[0].inIP == "192.0.2.10"
    assert call.RTPClients[0].inPort == 10000
    assert call.RTPClients[0].outIP == "198.51.100.10"
    assert call.RTPClients[0].outPort == 20000


def test_create_rtp_clients_is_idempotent_for_existing_local_port(monkeypatch):
    monkeypatch.setattr(RTP, "RTPClient", DummyRTPClient)
    call = make_call()
    codecs = {0: RTP.PayloadType.PCMU}

    call.create_rtp_clients(
        codecs,
        "192.0.2.10",
        10000,
        make_request("198.51.100.10"),
        20000,
    )
    call.create_rtp_clients(
        codecs,
        "192.0.2.10",
        10000,
        make_request("198.51.100.11"),
        20002,
    )

    assert len(call.RTPClients) == 1
    assert call.RTPClients[0].outIP == "198.51.100.10"
    assert call.RTPClients[0].outPort == 20000
