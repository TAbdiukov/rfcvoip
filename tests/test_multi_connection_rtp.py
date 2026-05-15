from threading import Lock

from rfcvoip import RTP, SIP
from rfcvoip.VoIP.VoIP import (
    CallState,
    VoIPCall,
    _expanded_media_connections,
)


def _sip_invite_with_sdp(body: str) -> SIP.SIPMessage:
    body_bytes = body.encode("utf8")
    packet = (
        "INVITE sip:100@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:200@example.com>;tag=remote\r\n"
        "To: <sip:100@example.com>\r\n"
        "Call-ID: multi-connection-call\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:200@192.0.2.10:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "\r\n"
    ).encode("utf8") + body_bytes
    return SIP.SIPMessage(packet)


def _multi_connection_sdp() -> str:
    return (
        "v=0\r\n"
        "o=remote 1 1 IN IP4 192.0.2.10\r\n"
        "s=multi\r\n"
        "c=IN IP4 224.2.1.1/127/3\r\n"
        "t=0 0\r\n"
        "m=audio 4000/3 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendrecv\r\n"
    )


class FakePhone:
    def __init__(self):
        self.rtpPortLow = 10000
        self.rtpPortHigh = 10010
        self.assignedPorts = []
        self.portsLock = Lock()
        self.codec_priorities = {}
        self.audio_sample_rate = None
        self.audio_sample_width = 1
        self.audio_channels = 1
        self.sip = object()
        self.calls = {}
        self.session_ids = []

    def request_ports(self, count, blocking=True):
        ports = list(range(10000, 10000 + int(count)))
        self.assignedPorts.extend(ports)
        return ports

    def reserve_ports(self, ports, *, allow_existing=None):
        allow_existing = set(allow_existing or set())
        for port in ports:
            if port in self.assignedPorts and port not in allow_existing:
                raise AssertionError(f"unexpected duplicate port {port}")
        for port in ports:
            if port not in self.assignedPorts:
                self.assignedPorts.append(port)
        return list(ports)

    def release_ports(self, call=None):
        return None


class FakeRTPClient:
    def __init__(
        self,
        assoc,
        inIP,
        inPort,
        outIP,
        outPort,
        sendrecv,
        **kwargs,
    ):
        self.assoc = assoc
        self.inIP = inIP
        self.inPort = inPort
        self.outIP = outIP
        self.outPort = outPort
        self.sendrecv = sendrecv
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True


def test_expands_sdp_connection_address_count():
    message = _sip_invite_with_sdp(_multi_connection_sdp())
    media = message.body["m"][0]

    connections = _expanded_media_connections(message, media)

    assert [connection["address"] for connection in connections] == [
        "224.2.1.1",
        "224.2.1.2",
        "224.2.1.3",
    ]


def test_incoming_multi_connection_media_creates_one_rtp_client_per_target(
    monkeypatch,
):
    monkeypatch.setattr("rfcvoip.VoIP.VoIP.RTP.RTPClient", FakeRTPClient)
    message = _sip_invite_with_sdp(_multi_connection_sdp())

    call = VoIPCall(
        FakePhone(),
        CallState.RINGING,
        message,
        12345,
        "127.0.0.1",
    )

    assert [
        (client.inPort, client.outIP, client.outPort)
        for client in call.RTPClients
    ] == [
        (10000, "224.2.1.1", 4000),
        (10001, "224.2.1.2", 4001),
        (10002, "224.2.1.3", 4002),
    ]

    media_answer = call.gen_ms()

    assert media_answer == {
        10000: {
            "media_type": "audio",
            "port_count": 3,
            "codecs": {0: RTP.PayloadType.PCMU},
        }
    }
    assert all(client.started for client in call.RTPClients)


def test_gen_answer_renders_single_port_count_media_section():
    request = _sip_invite_with_sdp(_multi_connection_sdp())
    client = SIP.SIPClient(
        "example.com",
        5060,
        "100",
        "secret",
        phone=object(),
        myIP="127.0.0.1",
    )
    client.tagLibrary[request.headers["Call-ID"]] = "localtag"

    answer = client.gen_answer(
        request,
        "12345",
        {
            10000: {
                "media_type": "audio",
                "port_count": 3,
                "codecs": {0: RTP.PayloadType.PCMU},
            }
        },
        RTP.TransmitType.SENDRECV,
    )

    assert answer.count("m=audio") == 1
    assert "m=audio 10000/3 RTP/AVP 0\r\n" in answer
    assert "a=rtpmap:0 PCMU/8000\r\n" in answer