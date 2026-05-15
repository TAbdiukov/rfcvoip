from types import SimpleNamespace

from rfcvoip import RTP
from rfcvoip.SIP import SIPClient


class DummyPhone:
    pass


def make_client(my_ip: str) -> SIPClient:
    return SIPClient(
        server="sip.example.com",
        port=5060,
        username="alice",
        password="secret",
        phone=DummyPhone(),
        myIP=my_ip,
        myPort=5060,
    )


def media_map():
    return {4000: {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT}}


def make_request(call_id: str = "call-123"):
    return SimpleNamespace(
        headers={
            "Via": [
                {
                    "address": ("198.51.100.20", "5060"),
                    "branch": "z9hG4bKbranch",
                    "rport": None,
                }
            ],
            "From": {"raw": "<sip:bob@example.com>", "tag": "remote-tag"},
            "To": {"raw": "<sip:alice@example.com>", "tag": ""},
            "Call-ID": call_id,
            "CSeq": {"check": "1", "method": "INVITE"},
        }
    )


def test_gen_invite_uses_ip4_in_sdp_for_ipv4_myip():
    client = make_client("192.0.2.10")
    invite = client.gen_invite(
        number="1001",
        sess_id="123",
        ms=media_map(),
        sendtype=RTP.TransmitType.SENDRECV,
        branch="z9hG4bKbranch",
        call_id="call-123",
    )

    assert "o=rfcvoip 123 125 IN IP4 192.0.2.10\r\n" in invite
    assert "c=IN IP4 192.0.2.10\r\n" in invite


def test_gen_invite_uses_ip6_in_sdp_for_ipv6_myip():
    client = make_client("2001:db8::10")
    invite = client.gen_invite(
        number="1001",
        sess_id="123",
        ms=media_map(),
        sendtype=RTP.TransmitType.SENDRECV,
        branch="z9hG4bKbranch",
        call_id="call-123",
    )

    assert "o=rfcvoip 123 125 IN IP6 2001:db8::10\r\n" in invite
    assert "c=IN IP6 2001:db8::10\r\n" in invite


def test_gen_answer_uses_ip6_in_sdp_for_ipv6_myip():
    client = make_client("2001:db8::10")
    request = make_request()
    client.tagLibrary[request.headers["Call-ID"]] = "local-tag"

    answer = client.gen_answer(
        request=request,
        sess_id="456",
        ms=media_map(),
        sendtype=RTP.TransmitType.SENDRECV,
    )

    assert "o=rfcvoip 456 458 IN IP6 2001:db8::10\r\n" in answer
    assert "c=IN IP6 2001:db8::10\r\n" in answer
