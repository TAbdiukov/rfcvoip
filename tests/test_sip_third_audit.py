from types import SimpleNamespace

import pytest

import rfcvoip.SIPTransport as transport_module
from rfcvoip.SIP import SIPClient, SIPMessage, SIPParseError
from rfcvoip.SIPTransport import (
    SIPConnection,
    SIPFramingError,
    SIPResolver,
    SIPTransport,
    ResolvedSIPTarget,
)


def _client():
    return SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
        myPort=5060,
    )


def _request(
    *,
    via="SIP/2.0/UDP 10.0.0.1:5070;branch=z9hG4bKtest",
    user="alice",
    version="SIP/2.0",
):
    return (
        f"OPTIONS sip:{user}@127.0.0.1 {version}\r\n"
        f"Via: {via}\r\n"
        f"From: <sip:{user}@example.test>;tag=remote\r\n"
        "To: <sip:alice@127.0.0.1>\r\n"
        "Call-ID: third-audit\r\nCSeq: 1 OPTIONS\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode("ascii")


@pytest.mark.parametrize(
    "user", ["alice;ext=2", "alice?desk", "alice;ext=2?desk"]
)
def test_uri_userinfo_delimiters_do_not_change_routing(user, monkeypatch):
    monkeypatch.setattr(transport_module, "dns_resolver", None)

    uri = f"sip:{user}@example.test:5070;transport=tcp?Subject=call"
    parsed = SIPResolver.parse_uri(uri)
    target = SIPResolver().resolve(uri)

    assert parsed.user == user
    assert parsed.host == "example.test"
    assert parsed.params == {"transport": "tcp"}
    assert target.host == "example.test"
    assert target.port == 5070
    assert target.transport is SIPTransport.TCP


@pytest.mark.parametrize(
    "user", ["alice;ext=2", "alice?desk", "alice;ext=2?desk"]
)
def test_address_header_preserves_userinfo_delimiters(user):
    request = SIPMessage(_request(user=user))

    assert request.headers["From"]["number"] == user
    assert request.headers["From"]["host"] == "example.test"


def test_later_at_sign_in_query_does_not_replace_actual_user_or_host():
    parsed = SIPResolver.parse_uri(
        "sip:alice?desk@example.test:5070?Subject=contact@work"
    )

    assert parsed.user == "alice?desk"
    assert parsed.host == "example.test"
    assert parsed.port == 5070


def test_via_parameters_are_case_insensitive_and_strip_separator_whitespace():
    client = _client()
    request = SIPMessage(
        _request(via="SIP/2.0/UDP 10.0.0.1:5070;Branch = z9hG4bKtest;RPORT")
    )
    client._apply_rport_received(request, ("192.0.2.10", 60000))

    assert request.headers["Via"][0]["branch"] == "z9hG4bKtest"
    assert client.response_target(request) == ("192.0.2.10", 60000)


@pytest.mark.parametrize(
    "name", ["address", "type", "transport", "parameters"]
)
def test_via_extension_cannot_override_parsed_routing_fields(name):
    client = _client()
    request = SIPMessage(
        _request(
            via=f"SIP/2.0/UDP 10.0.0.1:5070;branch=z9hG4bKtest;{name}=extension"
        )
    )

    response = client.gen_ok(request)

    assert client.response_target(request) == ("10.0.0.1", 5070)
    assert "Via: SIP/2.0/UDP 10.0.0.1:5070;" in response
    assert f";{name}=extension" in response


def test_via_quoted_extension_keeps_semicolons_inside_its_value():
    request = SIPMessage(
        _request(via='SIP/2.0/UDP 10.0.0.1:5070;branch=z9hG4bKtest;note="a;b"')
    )

    assert request.headers["Via"][0]["note"] == '"a;b"'
    assert ';note="a;b"\r\n' in _client().gen_ok(request)


def test_via_rejects_duplicate_case_insensitive_branch_parameters():
    with pytest.raises(SIPParseError, match="Duplicate.*Via"):
        SIPMessage(
            _request(
                via="SIP/2.0/UDP 10.0.0.1:5070;branch=first;Branch=second"
            )
        )


def test_unsupported_request_version_receives_505(monkeypatch):
    client = _client()
    sent = []
    client.connection = SimpleNamespace(
        recv_raw_message=lambda: _request(version="SIP/3.0"),
        last_recv_address=("192.0.2.10", 60000),
    )
    monkeypatch.setattr(
        client, "send_raw", lambda data, target: sent.append((data, target))
    )

    client.recv()

    assert len(sent) == 1
    assert sent[0][0].startswith(b"SIP/2.0 505 ")
    assert sent[0][1] == ("192.0.2.10", 60000)


def test_partial_stream_writes_obey_total_send_deadline(monkeypatch):
    clock = [0.0]
    sends = []

    def send(data):
        sends.append(bytes(data))
        clock[0] += 0.6
        return 1

    monkeypatch.setattr(transport_module.time, "monotonic", lambda: clock[0])
    connection = SIPConnection(
        "127.0.0.1",
        5060,
        ResolvedSIPTarget("example.test", 5060, SIPTransport.TCP),
        send_timeout=1.0,
    )
    connection.socket = SimpleNamespace(send=send)

    with pytest.raises(TimeoutError, match="Timed out"):
        connection.send(b"12345")

    assert len(sends) == 2


@pytest.mark.parametrize(
    "value", ["+0", "-0", "0_0", "\N{ARABIC-INDIC DIGIT ZERO}"]
)
def test_content_length_requires_ascii_decimal_digits(value):
    raw = _request().replace(
        b"Content-Length: 0", f"Content-Length: {value}".encode("utf-8")
    )

    with pytest.raises(SIPParseError, match="Content-Length"):
        SIPMessage(raw)


@pytest.mark.parametrize(
    "value", ["+0", "-0", "0_0", "\N{ARABIC-INDIC DIGIT ZERO}"]
)
def test_stream_content_length_requires_ascii_decimal_digits(value):
    connection = SIPConnection(
        "127.0.0.1",
        5060,
        ResolvedSIPTarget("example.test", 5060, SIPTransport.TCP),
    )
    connection._stream_buffer = (
        f"SIP/2.0 200 OK\r\nContent-Length: {value}\r\n\r\n".encode("utf-8")
    )

    with pytest.raises(SIPFramingError, match="Content-Length"):
        connection._stream_message_length()
