from rfcvoip.SIP import SIPClient, SIPMessage


class DummyPhone:
    _status = None


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def build_client():
    client = SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP="192.0.2.10",
        myPort=5060,
    )
    client.out = FakeSocket()
    return client


def build_request(method):
    raw = (
        f"{method} sip:alice@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 198.51.100.20:5080;branch=z9hG4bK-1;rport\r\n"
        "From: <sip:bob@example.com>;tag=from-tag\r\n"
        "To: <sip:alice@example.com>\r\n"
        "Call-ID: call-123\r\n"
        f"CSeq: 1 {method}\r\n"
        "Contact: <sip:bob@198.51.100.20:5080>\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    return SIPMessage(raw.encode("utf8"))


def test_parse_message_replies_501_to_unsupported_method():
    client = build_client()
    request = build_request("PUBLISH")

    client.parse_message(request)

    assert len(client.out.sent) == 1
    payload, target = client.out.sent[0]
    text = payload.decode("utf8")

    assert target == ("198.51.100.20", 5080)
    assert text.startswith("SIP/2.0 501 Not Implemented\r\n")
    assert "Call-ID: call-123\r\n" in text
    assert "CSeq: 1 PUBLISH\r\n" in text
    assert "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, SUBSCRIBE, NOTIFY\r\n" in text


def test_parse_message_keeps_existing_options_response():
    client = build_client()
    request = build_request("OPTIONS")

    client.parse_message(request)

    assert len(client.out.sent) == 1
    payload, _ = client.out.sent[0]

    assert payload.decode("utf8").startswith("SIP/2.0 200 OK\r\n")
