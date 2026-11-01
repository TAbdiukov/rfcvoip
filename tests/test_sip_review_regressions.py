from types import SimpleNamespace
import time

import pytest

import rfcvoip
import rfcvoip.SIP as sip_module
import rfcvoip.SIPTransport as transport_module
from rfcvoip.SIP import SIPClient, SIPMessage, SIPParseError
from rfcvoip.SIPAuth import (
    compute_digest_response,
    validate_nonce,
    verify_digest_response,
)
from rfcvoip.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPResolver,
    SIPTransport,
)
from rfcvoip.VoIP import VoIPPhone
from rfcvoip.VoIP.status import PhoneStatus


def _client():
    return SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
        myPort=0,
    )


def test_response_routes_to_received_host_without_rport():
    request = SIPMessage(
        b"OPTIONS sip:alice@example.test SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.1:5070;branch=z9hG4bKtest\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    client = _client()
    client._apply_rport_received(request, ("192.0.2.40", 60000))

    assert client.response_target(request) == ("192.0.2.40", 5070)


@pytest.mark.parametrize("maddr", ["192.0.2.40", "[2001:db8::40]"])
def test_maddr_preserves_uri_port(monkeypatch, maddr):
    monkeypatch.setattr(transport_module, "dns_resolver", None)

    target = SIPResolver().resolve(
        f"sip:alice@example.test:5070;maddr={maddr}"
    )

    assert target.host == maddr.strip("[]")
    assert target.port == 5070
    assert target.explicit_port is True


@pytest.mark.parametrize("failure_stage", ["open", "register"])
def test_start_failure_cleans_up_and_allows_retry(monkeypatch, failure_stage):
    instances = []

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            self.socket = object()
            self.closed = False
            instances.append(self)

        def open(self):
            if failure_stage == "open":
                raise OSError("cannot bind")

        def close(self):
            self.closed = True
            self.socket = None

    client = _client()
    monkeypatch.setattr(sip_module, "SIPConnection", FakeConnection)
    if failure_stage == "register":

        def fail_register():
            raise RuntimeError("registration failed")

        monkeypatch.setattr(client, "register", fail_register)

    with pytest.raises((OSError, RuntimeError)):
        client.start()

    assert client.NSD is False
    assert client.connection is None
    assert getattr(client, "s", None) is None
    assert getattr(client, "out", None) is None
    assert instances[0].closed

    # The original failure must not leave the client stuck as already started.
    with pytest.raises((OSError, RuntimeError)) as failure:
        client.start()
    assert "already started" not in str(failure.value)


def test_udp_bind_failure_closes_created_socket(monkeypatch):
    class FailingSocket:
        closed = False

        def bind(self, address):
            raise OSError("address already in use")

        def close(self):
            self.closed = True

    sock = FailingSocket()
    monkeypatch.setattr(transport_module.socket, "socket", lambda *args: sock)
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.UDP),
    )

    with pytest.raises(OSError, match="address already in use"):
        connection.open()

    assert sock.closed


@pytest.mark.parametrize(
    "nonce", ["\N{SNOWMAN}.signature", "payload.\N{SNOWMAN}"]
)
def test_nonce_validation_rejects_non_ascii_without_raising(nonce):
    assert validate_nonce(nonce, "secret") is False


@pytest.mark.parametrize("response", ["\N{SNOWMAN}", "not-a-digest", "f" * 31])
def test_digest_verification_rejects_malformed_response(response):
    authorization = {
        "username": "alice",
        "realm": "example.test",
        "nonce": "nonce",
        "uri": "sip:example.test",
        "response": response,
    }

    assert (
        verify_digest_response(
            authorization, method="REGISTER", password="secret"
        )
        is False
    )


@pytest.mark.parametrize("nc", [None, "", "1", "0000000g"])
def test_digest_verification_requires_valid_nonce_count_for_qop(nc):
    authorization = {
        "username": "alice",
        "realm": "example.test",
        "nonce": "nonce",
        "uri": "sip:example.test",
        "qop": "auth",
        "cnonce": "client",
    }
    if nc is not None:
        authorization["nc"] = nc
    authorization["response"] = compute_digest_response(
        authorization, method="REGISTER", password="secret"
    )

    assert (
        verify_digest_response(
            authorization, method="REGISTER", password="secret"
        )
        is False
    )


def test_digest_verification_rejects_qop_lists_in_authorization():
    authorization = {
        "username": "alice",
        "realm": "example.test",
        "nonce": "nonce",
        "uri": "sip:example.test",
        "qop": "auth,auth-int",
        "nc": "00000001",
        "cnonce": "client",
    }
    authorization["response"] = compute_digest_response(
        authorization, method="REGISTER", password="secret"
    )

    assert (
        verify_digest_response(
            authorization, method="REGISTER", password="secret"
        )
        is False
    )


@pytest.mark.parametrize(
    "body",
    [
        b"v=invalid\r\n",
        b"m=audio invalid RTP/AVP 0\r\n",
        b"c=IN IP4 224.0.0.1/\r\n",
    ],
)
def test_malformed_sdp_numbers_raise_sip_parse_error(body):
    raw = (
        b"SIP/2.0 200 OK\r\n"
        b"Content-Type: application/sdp\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )

    with pytest.raises(SIPParseError):
        SIPMessage(raw)


def test_response_wait_ignores_invalid_sdp_then_receives_valid_message():
    client = _client()
    client.NSD = True
    packets = iter(
        [
            b"SIP/2.0 200 OK\r\nContent-Type: application/sdp\r\n"
            b"Content-Length: 11\r\n\r\nv=invalid\r\n",
            b"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n",
        ]
    )
    client.connection = SimpleNamespace(
        recv_raw_message_before=lambda *args, **kwargs: next(packets),
    )

    response = client._recv_message_before(time.monotonic() + 1)

    assert response is not None
    assert response.body_raw == b""


def test_phone_start_preserves_stopped_state_after_fatal_registration(
    monkeypatch,
):
    phone = VoIPPhone("127.0.0.1", 5060, "alice", "secret", myIP="127.0.0.1")
    connection = SimpleNamespace(
        socket=object(),
        open=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr(
        sip_module, "SIPConnection", lambda *args, **kwargs: connection
    )
    monkeypatch.setattr(phone.sip, "_SIPClient__register", lambda: False)
    phone.sip.registerFailures = rfcvoip.REGISTER_FAILURE_THRESHOLD - 1

    phone.start()

    assert phone.NSD is False
    assert phone.sip.NSD is False
    assert phone.get_status() is PhoneStatus.FAILED
