from types import SimpleNamespace
import socket
import time

import pytest

import rfcvoip.SIPTransport as transport_module
import rfcvoip.SIP as sip_module
from rfcvoip.SIP import SIPClient, SIPMessage, SIPSubscription
from rfcvoip.SIPAuth import (
    SIPAuthError,
    build_digest_auth_header,
    choose_digest_challenge,
    compute_digest_response,
    parse_digest_params,
    verify_digest_response,
)
from rfcvoip.SIPTransport import ResolvedSIPTarget, SIPConnection, SIPTransport
from rfcvoip import RTP
from rfcvoip.VoIP import CallState, VoIPCall, VoIPPhone


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


@pytest.mark.parametrize("override", [None, "override.example.test"])
def test_tls_verifies_original_uri_host_after_dns_service_resolution(
    monkeypatch, override
):
    names = []
    sock = SimpleNamespace(
        settimeout=lambda timeout: None,
        bind=lambda address: None,
        connect=lambda address: None,
        close=lambda: None,
    )
    context = SimpleNamespace(
        wrap_socket=lambda sock, server_hostname: names.append(server_hostname)
        or sock,
    )
    monkeypatch.setattr(transport_module.socket, "socket", lambda *args: sock)
    monkeypatch.setattr(
        transport_module.socket,
        "getaddrinfo",
        lambda *args: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.5", 5061)),
        ],
    )
    connection = SIPConnection(
        "127.0.0.1",
        5060,
        ResolvedSIPTarget(
            "edge.provider.test",
            5061,
            SIPTransport.TLS,
            uri_host="example.test",
            source="srv-explicit-transport",
        ),
        tls_context=context,
        tls_server_name=override,
    )

    connection.open()

    assert names == [override or "example.test"]


def test_tls_receive_reads_decrypted_pending_bytes_before_select(monkeypatch):
    raw = b"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
    sock = SimpleNamespace(
        gettimeout=lambda: 30,
        setblocking=lambda blocking: None,
        settimeout=lambda timeout: None,
        pending=lambda: len(raw),
        recv=lambda size: raw,
    )
    monkeypatch.setattr(
        transport_module.select, "select", lambda *args: ([], [], [])
    )
    connection = SIPConnection(
        "127.0.0.1",
        5060,
        ResolvedSIPTarget("example.test", 5061, SIPTransport.TLS),
    )
    connection.socket = sock

    assert connection.recv_raw_message_before(time.monotonic() + 1) == raw


def _subscription():
    return SIPSubscription(
        call_id="subscription-test",
        target="bob",
        target_uri="sip:bob@example.test",
        event="presence",
        accept=["application/pidf+xml"],
        local_tag="local-tag",
        remote_tag="remote-tag",
    )


def _notify(*, to_tag="local-tag", from_tag="remote-tag", event="presence"):
    return SIPMessage(
        (
            "NOTIFY sip:alice@127.0.0.1 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.0.2.5:5060;branch=z9hG4bKnotify\r\n"
            f"From: <sip:bob@example.test>{';tag=' + from_tag if from_tag else ''}\r\n"
            f"To: <sip:alice@127.0.0.1>{';tag=' + to_tag if to_tag else ''}\r\n"
            "Call-ID: subscription-test\r\n"
            "CSeq: 1 NOTIFY\r\n"
            + (f"Event: {event}\r\n" if event else "")
            + "Subscription-State: terminated;reason=timeout\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("ascii")
    )


@pytest.mark.parametrize(
    "params",
    [
        {"to_tag": ""},
        {"from_tag": ""},
        {"from_tag": "unrelated-dialog"},
        {"event": ""},
        {"event": "message-summary"},
    ],
)
def test_notify_does_not_modify_an_unmatched_subscription(monkeypatch, params):
    client = _client()
    subscription = _subscription()
    client.subscriptions[subscription.call_id] = subscription
    responses = []
    monkeypatch.setattr(
        client,
        "_send_request_response",
        lambda request, response: responses.append(response),
    )

    client._handle_notify(_notify(**params))

    assert client.subscriptions.get(subscription.call_id) is subscription
    assert subscription.remote_tag == "remote-tag"
    assert subscription.subscription_state is None
    assert responses[0].startswith("SIP/2.0 481 ")


def test_failed_initial_subscribe_send_discards_pending_subscription(
    monkeypatch,
):
    client = _client()
    client.NSD = True
    old_tags = dict(client.tagLibrary)

    def fail_send(*args):
        raise OSError("signaling lost")

    monkeypatch.setattr(client, "send_raw", fail_send)

    with pytest.raises(OSError, match="signaling lost"):
        client.subscribe_to("bob")

    assert client.subscriptions == {}
    assert client.tagLibrary == old_tags


def test_failed_unsubscribe_send_restores_pending_expiry(monkeypatch):
    client = _client()
    client.NSD = True
    subscription = _subscription()
    client.subscriptions[subscription.call_id] = subscription

    def fail_send(*args):
        raise OSError("signaling lost")

    monkeypatch.setattr(client, "send_raw", fail_send)

    with pytest.raises(OSError, match="signaling lost"):
        client.unsubscribe_from(subscription.call_id)

    assert client.subscriptions[subscription.call_id] is subscription
    assert subscription.pending_expires == 3600


def test_terminated_subscription_discards_dialog_tag(monkeypatch):
    client = _client()
    subscription = _subscription()
    client.subscriptions[subscription.call_id] = subscription
    client.tagLibrary[subscription.call_id] = subscription.local_tag
    monkeypatch.setattr(client, "_send_request_response", lambda *args: None)

    client._handle_notify(_notify())

    assert subscription.call_id not in client.subscriptions
    assert subscription.call_id not in client.tagLibrary


@pytest.mark.parametrize(
    "unusable",
    [
        {
            "realm": "example.test",
            "nonce": "n",
            "algorithm": "SHA-256",
            "qop": "unsupported",
        },
        {"realm": "example.test", "algorithm": "SHA-256"},
        {"nonce": "n", "algorithm": "SHA-256"},
    ],
)
def test_digest_challenge_selection_skips_unusable_challenges(unusable):
    usable = {"realm": "example.test", "nonce": "n", "algorithm": "MD5"}

    assert choose_digest_challenge([unusable, usable]) == usable


def test_empty_digest_algorithm_policy_disables_authentication():
    challenge = {"realm": "example.test", "nonce": "n", "algorithm": "MD5"}

    assert (
        choose_digest_challenge([challenge], supported_algorithms=[]) is None
    )
    with pytest.raises(SIPAuthError, match="Unsupported"):
        compute_digest_response(
            challenge,
            username="alice",
            password="secret",
            method="REGISTER",
            uri="sip:example.test",
            supported_algorithms=[],
        )


def test_digest_header_accepts_single_pass_algorithm_iterable():
    header = build_digest_auth_header(
        {"realm": "example.test", "nonce": "n", "algorithm": "SHA-256"},
        header_name="Authorization",
        username="alice",
        password="secret",
        method="REGISTER",
        uri="sip:example.test",
        supported_algorithms=iter(["SHA-256"]),
    )

    assert "algorithm=SHA-256" in header


def test_digest_authentication_accepts_explicit_empty_realm():
    challenge = {"realm": "", "nonce": "nonce", "algorithm": "SHA-256"}
    assert choose_digest_challenge([challenge]) == challenge

    header = build_digest_auth_header(
        challenge,
        header_name="Authorization",
        username="alice",
        password="secret",
        method="REGISTER",
        uri="sip:example.test",
    )
    authorization = parse_digest_params(header.split(":", 1)[1])

    assert authorization["realm"] == ""
    assert verify_digest_response(
        authorization, method="REGISTER", password="secret"
    )


@pytest.mark.parametrize("realm", [None, "missing"])
def test_digest_header_still_rejects_absent_realm(realm):
    challenge = {"nonce": "nonce", "algorithm": "SHA-256"}
    if realm is None:
        challenge["realm"] = None

    assert choose_digest_challenge([challenge]) is None
    with pytest.raises(SIPAuthError, match="missing realm"):
        build_digest_auth_header(
            challenge,
            header_name="Authorization",
            username="alice",
            password="secret",
            method="REGISTER",
            uri="sip:example.test",
        )


@pytest.mark.parametrize(
    "contact_expiry, expected_delay", [(None, 55), (30, 25)]
)
def test_registration_refresh_uses_registrar_granted_expiry(
    monkeypatch, contact_expiry, expected_delay
):
    client = _client()
    client.NSD = True
    headers = "Expires: 60\r\n"
    if contact_expiry is not None:
        headers += (
            f"Contact: <{client._contact_uri()}>;expires={contact_expiry}\r\n"
        )
    response = SIPMessage(
        ("SIP/2.0 200 OK\r\n" + headers + "Content-Length: 0\r\n\r\n").encode(
            "ascii"
        )
    )
    monkeypatch.setattr(
        client, "_send_register_request", lambda *args, **kwargs: response
    )
    scheduled = []

    class FakeThread:
        def __init__(self, **kwargs):
            scheduled.append(kwargs["args"][0])

        def start(self):
            pass

    monkeypatch.setattr(sip_module, "Thread", FakeThread)

    assert client.register() is True

    assert scheduled == [expected_delay]
    assert client.default_expires == 120


@pytest.fixture
def real_dialog(monkeypatch):
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        callCallback=lambda call: None,
    )
    sdp = (
        "v=0\r\no=bob 1 1 IN IP4 127.0.0.1\r\ns=call\r\n"
        "c=IN IP4 127.0.0.1\r\nt=0 0\r\nm=audio 40000 RTP/AVP 0\r\n"
    )
    raw = (
        "INVITE sip:alice@127.0.0.1 SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5070;branch=z9hG4bKoriginal\r\n"
        "From: <sip:bob@example.test>;tag=remote\r\n"
        "To: <sip:alice@127.0.0.1>\r\nCall-ID: real-dialog\r\n"
        "CSeq: 42 INVITE\r\nContent-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp)}\r\n\r\n" + sdp
    )
    request = SIPMessage(raw.encode("ascii"))
    phone.sip.tagLibrary["real-dialog"] = "local"
    call = VoIPCall(
        phone, CallState.RINGING, request, 123, phone.myIP, sendmode="sendrecv"
    )
    phone._set_call(call.call_id, call)
    sent = []
    monkeypatch.setattr(
        phone.sip,
        "_send_request_response",
        lambda request, response: sent.append(response),
    )
    monkeypatch.setattr(RTP.RTPClient, "start", lambda self: None)
    yield phone, call, sent
    call._stop_rtp_clients()
    call._finalize_ended_call()


def _dialog_request(
    method,
    *,
    to_tag="local",
    from_tag="remote",
    branch="z9hG4bKoriginal",
    cseq=42,
):
    return SIPMessage(
        (
            f"{method} sip:alice@127.0.0.1 SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP 127.0.0.1:5070;branch={branch}\r\n"
            f"From: <sip:bob@example.test>{';tag=' + from_tag if from_tag else ''}\r\n"
            f"To: <sip:alice@127.0.0.1>{';tag=' + to_tag if to_tag else ''}\r\n"
            f"Call-ID: real-dialog\r\nCSeq: {cseq} {method}\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("ascii")
    )


@pytest.mark.parametrize(
    "params",
    [
        {"to_tag": "wrong"},
        {"from_tag": "wrong"},
        {"to_tag": ""},
        {"from_tag": ""},
    ],
)
def test_bye_with_wrong_dialog_tags_preserves_real_call(real_dialog, params):
    phone, call, sent = real_dialog
    call.answer()
    sent.clear()

    phone.sip.parse_message(_dialog_request("BYE", **params))

    assert call.state is CallState.ANSWERED
    assert phone._get_call(call.call_id) is call
    assert sent[0].startswith("SIP/2.0 481 ")


def test_matching_bye_ends_real_call(real_dialog):
    phone, call, sent = real_dialog
    call.answer()
    sent.clear()

    phone.sip.parse_message(_dialog_request("BYE"))

    assert call.state is CallState.ENDED
    assert phone._get_call(call.call_id) is None
    assert sent[0].startswith("SIP/2.0 200 ")


def test_bye_matches_outbound_dialog_tag_orientation(real_dialog):
    phone, call, sent = real_dialog
    call.answer()
    call.request = SIPMessage(
        call.request.raw.replace(
            b"From: <sip:bob@example.test>;tag=remote",
            b"From: <sip:alice@127.0.0.1>;tag=local",
        ).replace(
            b"To: <sip:alice@127.0.0.1>",
            b"To: <sip:bob@example.test>",
        )
    )
    call.remote_sip_message = SIPMessage(
        b"SIP/2.0 200 OK\r\nFrom: <sip:alice@127.0.0.1>;tag=local\r\n"
        b"To: <sip:bob@example.test>;tag=remote\r\n"
        b"Call-ID: real-dialog\r\nCSeq: 42 INVITE\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    sent.clear()

    phone.sip.parse_message(_dialog_request("BYE"))

    assert call.state is CallState.ENDED
    assert sent[0].startswith("SIP/2.0 200 ")


@pytest.mark.parametrize(
    "params",
    [
        {"branch": "z9hG4bKother"},
        {"cseq": 43},
        {"from_tag": "wrong"},
    ],
)
def test_cancel_with_wrong_transaction_preserves_real_call(
    real_dialog, params
):
    phone, call, sent = real_dialog

    phone.sip.parse_message(_dialog_request("CANCEL", to_tag="", **params))

    assert call.state is CallState.RINGING
    assert phone._get_call(call.call_id) is call
    assert sent[0].startswith("SIP/2.0 481 ")


def test_matching_cancel_ends_real_call(real_dialog):
    phone, call, sent = real_dialog

    phone.sip.parse_message(_dialog_request("CANCEL", to_tag=""))

    assert call.state is CallState.ENDED
    assert phone._get_call(call.call_id) is None
    assert sent[0].startswith("SIP/2.0 200 ")
    assert sent[1].startswith("SIP/2.0 487 ")


def test_matching_cancel_allows_equivalent_address_header_formatting(
    real_dialog,
):
    phone, call, sent = real_dialog
    cancel = _dialog_request("CANCEL", to_tag="")
    cancel = SIPMessage(
        cancel.raw.replace(
            b"From: <sip:bob@example.test>;tag=remote",
            b'From: "Bob" <sip:bob@EXAMPLE.TEST>;tag=remote',
        ).replace(
            b"To: <sip:alice@127.0.0.1>",
            b"To: sip:alice@127.0.0.1",
        )
    )

    phone.sip.parse_message(cancel)

    assert call.state is CallState.ENDED
    assert sent[0].startswith("SIP/2.0 200 ")
