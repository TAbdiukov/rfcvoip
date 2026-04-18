from pyVoIP import SIP
import pyVoIP.VoIP.VoIP as voip_module
from pyVoIP.VoIP.VoIP import VoIPPhone


class FakeTimer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.name = ""
        self.daemon = False
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


def build_invite(call_id: str, payloads: str, rtpmap_lines: str = "") -> SIP.SIPMessage:
    body = (
        "v=0\r\n"
        "o=- 1 1 IN IP4 192.0.2.55\r\n"
        "s=-\r\n"
        "c=IN IP4 192.0.2.55\r\n"
        "t=0 0\r\n"
        f"m=audio 49170 RTP/AVP {payloads}\r\n"
        f"{rtpmap_lines}"
        "a=sendrecv\r\n"
    )
    raw = (
        "INVITE sip:alice@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.55:5060;branch=z9hG4bK-1\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:bob@example.com>;tag=caller\r\n"
        "To: <sip:alice@example.com>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.55:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body.encode('utf8'))}\r\n\r\n"
        f"{body}"
    )
    return SIP.SIPMessage(raw.encode("utf8"))


def test_event_only_invite_is_rejected_with_488(monkeypatch):
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        callCallback=lambda call: None,
    )
    responses = []
    monkeypatch.setattr(
        phone.sip,
        "send_response",
        lambda request, response: responses.append(response),
    )

    request = build_invite(
        "call-event-only",
        "101",
        "a=rtpmap:101 telephone-event/8000\r\n",
    )

    phone._callback_MSG_Invite(request)

    assert responses
    assert responses[0].startswith("SIP/2.0 488 Not Acceptable Here")
    assert "180 Ringing" not in responses[0]
    assert "call-event-only" not in phone.calls


def test_supported_invite_still_rings_and_creates_call(monkeypatch):
    monkeypatch.setattr(voip_module, "Timer", FakeTimer)

    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        callCallback=lambda call: None,
    )
    responses = []
    monkeypatch.setattr(
        phone.sip,
        "send_response",
        lambda request, response: responses.append(response),
    )

    request = build_invite(
        "call-pcmu",
        "0 101",
        "a=rtpmap:101 telephone-event/8000\r\n",
    )

    phone._callback_MSG_Invite(request)

    assert responses
    assert responses[0].startswith("SIP/2.0 180 Ringing")
    assert "call-pcmu" in phone.calls
