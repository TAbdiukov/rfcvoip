from types import SimpleNamespace

import pytest

from rfcvoip import RTP
from rfcvoip.SIP import (
    InvalidAccountInfoError,
    SIPClient,
    SIPMessage,
    SIPParseError,
    SIPSubscription,
)
from rfcvoip.SIPAuth import verify_digest_response


def client():
    instance = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
        auth_username="proxy-alice",
    )
    instance.NSD = True
    return instance


def response(request, status, *, nonce="nonce", both_challenges=False):
    to_tag = request.headers["To"]["tag"] or "remote"
    headers = (
        f"SIP/2.0 {status} Response\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:5060;branch={request.headers['Via'][0]['branch']}\r\n"
        f"From: {request.headers['From']['raw']};tag={request.headers['From']['tag']}\r\n"
        f"To: {request.headers['To']['raw']};tag={to_tag}\r\n"
        f"Call-ID: {request.headers['Call-ID']}\r\n"
        f"CSeq: {request.headers['CSeq']['check']} {request.method}\r\n"
        "Contact: <sip:bob@127.0.0.1:5070>\r\n"
    )
    if status == 401 or both_challenges:
        headers += f'WWW-Authenticate: Digest realm="origin", nonce="{nonce}", qop="auth"\r\n'
    if status == 407 or both_challenges:
        headers += 'Proxy-Authenticate: Digest realm="proxy", nonce="proxy-nonce", qop="auth"\r\n'
    return SIPMessage((headers + "Content-Length: 0\r\n\r\n").encode())


def signaling(monkeypatch, instance, replies):
    sent = []
    remaining = list(replies)
    monkeypatch.setattr(
        instance,
        "send_raw",
        lambda raw, target: sent.append(SIPMessage(raw)),
    )

    def receive(deadline):
        if not remaining:
            instance.NSD = False
            return None
        request = next(item for item in reversed(sent) if item.method != "ACK")
        code, options = remaining.pop(0)
        return response(request, code, **options)

    monkeypatch.setattr(instance, "_recv_message_before", receive)
    return sent


@pytest.mark.parametrize("last_nonce", ["fresh", "first"])
def test_invite_can_complete_configured_multiple_digest_retries(
    monkeypatch, last_nonce
):
    instance = client()
    instance.invite_max_retries = 2
    sent = signaling(
        monkeypatch,
        instance,
        [
            (401, {"nonce": "first"}),
            (401, {"nonce": last_nonce}),
            (200, {}),
        ],
    )

    invite, call_id, session = instance.invite(
        "bob",
        {40000: {0: RTP.PayloadType.PCMU}},
        RTP.TransmitType.SENDRECV,
    )

    requests = [item for item in sent if item.method == "INVITE"]
    assert len(requests) == 3
    assert [item.headers["CSeq"]["check"] for item in requests] == [
        "1",
        "2",
        "3",
    ]
    assert len({item.headers["Via"][0]["branch"] for item in requests}) == 3
    assert invite.headers["Authorization"]["nonce"] == last_nonce
    assert invite.headers["Authorization"]["nc"] == (
        "00000002" if last_nonce == "first" else "00000001"
    )
    assert verify_digest_response(
        invite.headers["Authorization"], method="INVITE", password="secret"
    )
    assert [item.method for item in sent] == [
        "INVITE",
        "ACK",
        "INVITE",
        "ACK",
        "INVITE",
    ]
    assert instance.pop_pending_invite_response(call_id).status.value == 200


@pytest.mark.parametrize(
    "operation", ["invite", "options", "subscribe", "unsubscribe"]
)
def test_digest_retries_keep_proxy_credentials_when_origin_also_challenges(
    monkeypatch, operation
):
    instance = client()
    instance.invite_max_retries = instance.options_max_retries = (
        instance.subscription_max_retries
    ) = 2
    sent = signaling(monkeypatch, instance, [(407, {}), (401, {}), (200, {})])
    if operation == "invite":
        instance.invite(
            "bob",
            {40000: {0: RTP.PayloadType.PCMU}},
            RTP.TransmitType.SENDRECV,
        )
        method = "INVITE"
    elif operation == "options":
        instance.options("bob")
        method = "OPTIONS"
    elif operation == "subscribe":
        instance.subscribe_to("bob")
        method = "SUBSCRIBE"
    else:
        subscription = SIPSubscription(
            call_id="unsubscribe-auth",
            target="bob",
            target_uri="sip:bob@127.0.0.1",
            event="presence",
            accept=["text/plain"],
            local_tag="local",
            remote_tag="remote",
        )
        instance.subscriptions[subscription.call_id] = subscription
        instance.unsubscribe_from(subscription.call_id)
        method = "SUBSCRIBE"

    requests = [item for item in sent if item.method == method]
    assert len(requests) == 3
    assert requests[-1].headers["Authorization"]["username"] == "alice"
    assert (
        requests[-1].headers["Proxy-Authorization"]["username"]
        == "proxy-alice"
    )
    assert requests[-2].headers["Proxy-Authorization"]["nc"] == "00000001"
    assert requests[-1].headers["Proxy-Authorization"]["nc"] == "00000002"
    assert verify_digest_response(
        requests[-1].headers["Proxy-Authorization"],
        method=method,
        password="secret",
    )


@pytest.mark.parametrize(
    "operation", ["invite", "options", "subscribe", "register"]
)
def test_401_uses_origin_challenge_when_proxy_header_is_also_present(
    monkeypatch, operation
):
    instance = client()
    sent = signaling(
        monkeypatch, instance, [(401, {"both_challenges": True}), (200, {})]
    )
    if operation == "invite":
        instance.invite(
            "bob",
            {40000: {0: RTP.PayloadType.PCMU}},
            RTP.TransmitType.SENDRECV,
        )
    elif operation == "options":
        instance.options("bob")
    elif operation == "subscribe":
        instance.subscribe_to("bob")
    else:
        # Exercise authenticated REGISTER generation without scheduling refresh.
        original = SIPMessage(instance.gen_first_response().encode())
        challenge = response(original, 401, both_challenges=True)
        sent = [SIPMessage(instance.gen_register(challenge).encode())]

    request = next(item for item in reversed(sent) if item.method != "ACK")
    assert request.headers["Authorization"]["realm"] == "origin"
    assert request.headers["Authorization"]["username"] == "alice"


@pytest.mark.parametrize("status", [200, 401])
@pytest.mark.parametrize("use_attribute", [False, True])
@pytest.mark.parametrize("separator", [":", " :"])
def test_ack_copies_original_invite_credentials(
    status, use_attribute, separator
):
    instance = client()
    raw = instance.gen_invite(
        "bob",
        "1",
        {40000: {0: RTP.PayloadType.PCMU}},
        RTP.TransmitType.SENDRECV,
        "z9hG4bKauth",
        "auth-call",
    )
    credentials = (
        f'Authorization{separator} Digest username="alice", realm="origin", nonce="n", response="abc"\r\n'
        'Proxy-Authorization: Digest username="proxy-alice", realm="proxy",\r\n'
        ' nonce="p", response="def"\r\n'
    )
    invite = SIPMessage(
        raw.replace(
            "Content-Length:", credentials + "Content-Length:"
        ).encode()
    )
    answer = response(invite, status)
    if use_attribute:
        answer._rfcvoip_invite_request = invite
        ack = instance.gen_ack(answer)
    else:
        ack = instance.gen_ack(answer, invite_request=invite)

    assert credentials in ack
    parsed = SIPMessage(ack.encode())
    assert parsed.headers["Authorization"] == invite.headers["Authorization"]
    assert (
        parsed.headers["Proxy-Authorization"]
        == invite.headers["Proxy-Authorization"]
    )


@pytest.mark.parametrize("status", [401, 407])
def test_digest_retry_rejects_challenge_for_wrong_authentication_hop(status):
    instance = client()
    original = SIPMessage(instance.gen_first_response().encode())
    wrong_challenge = response(original, 407 if status == 401 else 401)
    wrong_challenge = SIPMessage(
        wrong_challenge.raw.replace(
            f"SIP/2.0 {int(wrong_challenge.status)}".encode(),
            f"SIP/2.0 {status}".encode(),
            1,
        )
    )

    with pytest.raises(SIPParseError, match="challenge"):
        instance.gen_register(wrong_challenge)


@pytest.mark.parametrize("operation", ["register", "deregister"])
@pytest.mark.parametrize("challenges", [(407, 401), (401, 407)])
def test_registration_authenticates_both_proxy_and_registrar(
    monkeypatch,
    operation,
    challenges,
):
    instance = client()
    sent = []
    remaining = list(challenges) + [200]

    def send_register(raw, *, action):
        request = SIPMessage(raw.encode())
        sent.append(request)
        return response(request, remaining.pop(0))

    monkeypatch.setattr(instance, "_send_register_request", send_register)

    assert getattr(instance, "_SIPClient__" + operation)() is True

    assert len(sent) == 3
    assert len({item.headers["Call-ID"] for item in sent}) == 1
    assert [item.headers["CSeq"]["check"] for item in sent] == ["1", "2", "3"]
    assert len({item.headers["Via"][0]["branch"] for item in sent}) == 3
    assert sent[-1].headers["Authorization"]["username"] == "alice"
    assert sent[-1].headers["Proxy-Authorization"]["username"] == "proxy-alice"
    first_header = (
        "Proxy-Authorization" if challenges[0] == 407 else "Authorization"
    )
    assert sent[-1].headers[first_header]["nc"] == "00000002"
    for header in ("Authorization", "Proxy-Authorization"):
        assert verify_digest_response(
            sent[-1].headers[header],
            method="REGISTER",
            password="secret",
        )
    if operation == "deregister":
        assert all(item.headers["Expires"] == "0" for item in sent)


@pytest.mark.parametrize("operation", ["register", "deregister"])
@pytest.mark.parametrize("status", [401, 407])
def test_repeated_registration_challenge_fails_without_retry_loop(
    monkeypatch,
    operation,
    status,
):
    instance = client()
    sent = []

    def send_register(raw, *, action):
        request = SIPMessage(raw.encode())
        sent.append(request)
        return response(request, status)

    monkeypatch.setattr(instance, "_send_register_request", send_register)

    with pytest.raises(InvalidAccountInfoError):
        getattr(instance, "_SIPClient__" + operation)()

    assert len(sent) == 2
