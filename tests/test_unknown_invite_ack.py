import pytest

from rfcvoip import SIP
from rfcvoip.VoIP.VoIP import VoIPPhone


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))


def invite_response(status_code, phrase):
    raw = (
        f"SIP/2.0 {status_code} {phrase}\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:alice@example.com>;tag=localtag\r\n"
        "To: <sip:bob@example.com>;tag=remotetag\r\n"
        "Call-ID: missing-call@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@127.0.0.1:5060>\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    )
    return SIP.SIPMessage(raw.encode("utf8"))


@pytest.mark.parametrize(
    "status_code,phrase,callback_name,request_uri",
    [
        (200, "OK", "_callback_RESP_OK", "sip:bob@127.0.0.1:5060"),
        (404, "Not Found", "_callback_RESP_NotFound", "sip:bob@example.com"),
        (
            503,
            "Service Unavailable",
            "_callback_RESP_Unavailable",
            "sip:bob@example.com",
        ),
    ],
)
def test_unknown_final_invite_response_is_acked_without_tag_library_entry(
    status_code,
    phrase,
    callback_name,
    request_uri,
):
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
    )
    fake_socket = FakeSocket()
    phone.sip.out = fake_socket

    response = invite_response(status_code, phrase)
    assert response.headers["Call-ID"] not in phone.calls
    assert response.headers["Call-ID"] not in phone.sip.tagLibrary

    getattr(phone, callback_name)(response)

    assert len(fake_socket.sent) == 1
    packet, address = fake_socket.sent[0]
    ack = packet.decode("utf8")

    assert address == ("127.0.0.1", 5060)
    assert ack.startswith(f"ACK {request_uri} SIP/2.0\r\n")
    assert "From: <sip:alice@example.com>;tag=localtag\r\n" in ack
    assert "To: <sip:bob@example.com>;tag=remotetag\r\n" in ack
    assert "CSeq: 1 ACK\r\n" in ack
