from types import SimpleNamespace

import pytest

import rfcvoip.SIP as sip_module
from rfcvoip.SIP import SIPClient, SIPMessage, SIPParseError


def request(method="OPTIONS", *, cseq="1", cseq_method=None):
    return (
        f"{method} sip:alice@127.0.0.1 SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.5:5070;branch=z9hG4bKdispatch\r\n"
        "From: <sip:bob@example.test>;tag=remote\r\n"
        "To: <sip:alice@127.0.0.1>;tag=local\r\n"
        "Call-ID: dispatch-validation\r\n"
        f"CSeq: {cseq} {cseq_method or method}\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode("ascii")


def receiver(monkeypatch, packets):
    dispatched = []
    sent = []
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        phone=SimpleNamespace(calls={}),
        callCallback=dispatched.append,
    )
    packets = iter(packets)
    client.connection = SimpleNamespace(
        recv_raw_message=lambda: next(packets),
        last_recv_address=("192.0.2.5", 5070),
    )
    monkeypatch.setattr(
        client, "send_raw", lambda data, target: sent.append(data)
    )
    return client, sent, dispatched


@pytest.mark.parametrize(
    "method,header",
    [
        ("OPTIONS", "Via"),
        ("OPTIONS", "From"),
        ("OPTIONS", "To"),
        ("OPTIONS", "Call-ID"),
        ("OPTIONS", "CSeq"),
        ("INVITE", "From"),
        ("BYE", "From"),
        ("CANCEL", "From"),
        ("PUBLISH", "From"),
    ],
)
def test_incomplete_request_cannot_break_receiver_or_reach_callback(
    monkeypatch, method, header
):
    prefix = (header + ":").encode("ascii")
    malformed = b"\r\n".join(
        line
        for line in request(method).split(b"\r\n")
        if not line.startswith(prefix)
    )
    client, sent, dispatched = receiver(monkeypatch, [malformed, request()])

    client.recv()
    client.recv()

    assert dispatched == []
    assert len(sent) == 1
    assert sent[0].startswith(b"SIP/2.0 200 ")


@pytest.mark.parametrize(
    "method", ["OPTIONS", "INVITE", "BYE", "CANCEL", "PUBLISH"]
)
@pytest.mark.parametrize(
    "cseq,cseq_method",
    [
        ("invalid", None),
        ("-1", None),
        ("2147483648", None),
        ("+1", None),
        ("1_0", None),
        ("1", "REGISTER"),
    ],
)
def test_invalid_request_sequence_is_rejected_before_dispatch(
    monkeypatch, method, cseq, cseq_method
):
    client, sent, dispatched = receiver(
        monkeypatch, [request(method, cseq=cseq, cseq_method=cseq_method)]
    )

    client.recv()

    assert dispatched == []
    assert len(sent) == 1
    assert sent[0].startswith(b"SIP/2.0 400 ")


def test_malformed_ack_is_ignored_without_a_response(monkeypatch):
    client, sent, dispatched = receiver(
        monkeypatch, [request("ACK", cseq="invalid")]
    )

    client.recv()

    assert sent == []
    assert dispatched == []


@pytest.mark.parametrize("cseq", ["0", "00001", "2147483647"])
def test_valid_sequence_boundaries_and_leading_zeroes_are_accepted(
    monkeypatch, cseq
):
    client, sent, dispatched = receiver(monkeypatch, [request(cseq=cseq)])

    client.recv()

    assert dispatched == []
    assert sent[0].startswith(b"SIP/2.0 200 ")


@pytest.mark.parametrize(
    "start_line",
    [
        b"OPTIONS sip:alice@127.0.0.1 SIP/2.0 ignored SIP/2.0",
        b"OPTIONS  SIP/2.0",
        b" sip:alice@127.0.0.1 SIP/2.0",
    ],
)
def test_malformed_request_start_line_is_rejected(start_line):
    _old, remainder = request().split(b"\r\n", 1)

    with pytest.raises(SIPParseError):
        SIPMessage(start_line + b"\r\n" + remainder)


def test_cseq_extra_tokens_are_rejected():
    raw = request().replace(b"CSeq: 1 OPTIONS", b"CSeq: 1 OPTIONS extra")

    with pytest.raises(SIPParseError, match="CSeq"):
        SIPMessage(raw)


@pytest.mark.parametrize("host", ["192.0.2.5", "[2001:db8::5]"])
@pytest.mark.parametrize(
    "port", [-1, 0, 65536, 99999999999999999999, "invalid"]
)
def test_via_destination_port_must_fit_udp_range(host, port):
    raw = request().replace(
        b"192.0.2.5:5070", f"{host}:{port}".encode("ascii")
    )

    with pytest.raises(SIPParseError, match="Via.*port"):
        SIPMessage(raw)


@pytest.mark.parametrize(
    "port", ["-1", "0", "65536", "99999999999999999999", "invalid", ""]
)
def test_rport_destination_must_be_a_valid_port(port):
    raw = request().replace(
        b";branch=", f";rport={port};branch=".encode("ascii")
    )

    with pytest.raises(SIPParseError, match="rport"):
        SIPMessage(raw)


@pytest.mark.parametrize("port", [1, 65535])
def test_via_and_rport_boundary_ports_remain_valid(port):
    raw = request().replace(
        b"192.0.2.5:5070", f"192.0.2.5:{port}".encode("ascii")
    )
    raw = raw.replace(b";branch=", f";rport={port};branch=".encode("ascii"))

    message = SIPMessage(raw)

    assert message.headers["Via"][0]["address"] == ("192.0.2.5", str(port))
    assert message.headers["Via"][0]["rport"] == str(port)


@pytest.mark.parametrize(
    "parameter",
    [b":99999999999999999999", b":5070;rport=99999999999999999999"],
)
def test_invalid_destination_port_does_not_stop_subsequent_receive(
    monkeypatch, parameter
):
    invalid = request().replace(b":5070;branch=", parameter + b";branch=")
    client, sent, dispatched = receiver(monkeypatch, [invalid, request()])

    client.recv()
    client.recv()

    assert dispatched == []
    assert len(sent) == 1
    assert sent[0].startswith(b"SIP/2.0 200 ")


def test_ephemeral_signaling_port_is_advertised_after_start(monkeypatch):
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        myPort=0,
        phone=SimpleNamespace(),
    )
    monkeypatch.setattr(client, "register", lambda: True)
    monkeypatch.setattr(
        sip_module,
        "Thread",
        lambda **kwargs: SimpleNamespace(start=lambda: None),
    )

    try:
        client.start()
        bound_port = client.connection.socket.getsockname()[1]

        assert client.myPort == bound_port
        registration = SIPMessage(client.gen_first_response().encode("utf8"))
        assert registration.headers["Via"][0]["address"][1] == str(bound_port)
        assert f":{bound_port};transport=" in registration.headers["Contact"]
    finally:
        client.NSD = False
        client._close_sockets()
