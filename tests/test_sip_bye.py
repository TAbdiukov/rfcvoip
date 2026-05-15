from types import SimpleNamespace

from rfcvoip.SIP import SIPClient
from rfcvoip.SIP import SIPMessage


def _client(**kwargs):
    return SIPClient(
        "registrar.example.com",
        5060,
        "bob",
        "secret",
        phone=SimpleNamespace(),
        myIP="192.0.2.10",
        myPort=5060,
        **kwargs,
    )


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, payload, target):
        self.sent.append((payload.decode("utf8"), target))


def test_gen_bye_uses_remote_from_uri_without_contact_for_inbound_call():
    client = _client()
    request = SIPMessage(
        (
            "INVITE sip:bob@192.0.2.10 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 203.0.113.9:5060;"
            "branch=z9hG4bKremote;rport\r\n"
            "From: <sip:alice@203.0.113.9>;tag=remote-tag\r\n"
            "To: <sip:bob@192.0.2.10>\r\n"
            "Call-ID: inbound-call\r\n"
            "CSeq: 314 INVITE\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("utf8")
    )
    client.tagLibrary["inbound-call"] = "local-tag"

    bye = client.gen_bye(request)

    assert bye.startswith("BYE sip:alice@203.0.113.9 SIP/2.0\r\n")
    assert (
        "\r\nVia: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK"
        in bye
    )
    assert "branch=z9hG4bKremote" not in bye
    assert "From: <sip:bob@192.0.2.10>;tag=local-tag\r\n" in bye
    assert "To: <sip:alice@203.0.113.9>;tag=remote-tag\r\n" in bye
    assert client.dialog_target(request) == ("203.0.113.9", 5060)


def test_bye_sends_to_contact_target_for_answered_outbound_call():
    client = _client()
    response = SIPMessage(
        (
            "SIP/2.0 200 OK\r\n"
            "Via: SIP/2.0/UDP 192.0.2.10:5060;"
            "branch=z9hG4bKlocal;rport\r\n"
            "From: <sip:bob@192.0.2.10>;tag=local-tag\r\n"
            "To: <sip:alice@example.com>;tag=remote-tag\r\n"
            "Call-ID: outbound-call\r\n"
            "CSeq: 1 INVITE\r\n"
            "Contact: <sip:alice@203.0.113.9:5070>\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("utf8")
    )
    client.tagLibrary["outbound-call"] = "local-tag"
    client.out = _FakeSocket()

    client.bye(response)

    payload, target = client.out.sent[0]
    assert target == ("203.0.113.9", 5070)
    assert payload.startswith(
        "BYE sip:alice@203.0.113.9:5070 SIP/2.0\r\n"
    )
    assert (
        "\r\nVia: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK"
        in payload
    )
