from rfcvoip.SIP import SIPClient, SIPMessage


class DummyPhone:
    def __init__(self, calls=None):
        self.calls = {} if calls is None else calls
        self._status = None


class DummySocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class DummyBlockingSocket:
    def setblocking(self, flag):
        self.flag = flag


def make_client(*, calls=None, callback=None):
    phone = DummyPhone(calls=calls)
    client = SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=phone,
        myIP="192.0.2.10",
        myPort=5060,
        callCallback=callback,
    )
    client.out = DummySocket()
    client.s = DummyBlockingSocket()
    return client


def make_request(method: str, call_id: str = "call-123@example.com") -> SIPMessage:
    return SIPMessage(
        (
            f"{method} sip:alice@example.com SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 198.51.100.10:5060;branch=z9hG4bK123\r\n"
            "From: <sip:bob@example.com>;tag=fromtag\r\n"
            "To: <sip:alice@example.com>;tag=totag\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 1 {method}\r\n"
            "Contact: <sip:bob@198.51.100.10:5060>\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("utf8")
    )


def sent_text(client: SIPClient) -> str:
    assert client.out.sent
    return client.out.sent[-1][0].decode("utf8")


def test_unknown_bye_returns_481():
    client = make_client(calls={})

    client.parse_message(make_request("BYE"))

    assert "SIP/2.0 481 Call/Transaction Does Not Exist" in sent_text(client)


def test_known_bye_returns_200_and_invokes_callback():
    seen = []
    client = make_client(
        calls={"call-123@example.com": object()},
        callback=lambda message: seen.append(message.method),
    )

    client.parse_message(make_request("BYE"))

    assert seen == ["BYE"]
    assert "SIP/2.0 200 OK" in sent_text(client)


def test_unknown_cancel_returns_481():
    client = make_client(calls={})

    client.parse_message(make_request("CANCEL"))

    assert "SIP/2.0 481 Call/Transaction Does Not Exist" in sent_text(client)


def test_known_cancel_returns_200_and_invokes_callback():
    seen = []
    client = make_client(
        calls={"call-123@example.com": object()},
        callback=lambda message: seen.append(message.method),
    )

    client.parse_message(make_request("CANCEL"))

    assert seen == ["CANCEL"]
    assert "SIP/2.0 200 OK" in sent_text(client)
