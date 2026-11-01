from types import SimpleNamespace

import pytest

import rfcvoip.SIP as sip_module
from rfcvoip.SIP import SIPClient, SIPMessage, SIPSubscription


@pytest.fixture
def subscription_client(monkeypatch):
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
    )
    subscription = SIPSubscription(
        call_id="notify-sequence",
        target="bob",
        target_uri="sip:bob@example.test",
        event="presence",
        accept=["text/plain"],
        local_tag="local",
        remote_tag="remote",
    )
    client.subscriptions[subscription.call_id] = subscription
    client.tagLibrary[subscription.call_id] = subscription.local_tag
    sent = []
    events = []
    monkeypatch.setattr(
        client,
        "_send_request_response",
        lambda request, response: sent.append(response),
    )
    client.subscription_callback = events.append
    return client, subscription, sent, events


def notify(
    cseq=10,
    *,
    state="active;expires=120",
    body="available",
    branch=None,
    method="NOTIFY",
):
    headers = (
        "NOTIFY sip:alice@127.0.0.1 SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 192.0.2.5:5060;branch={branch or 'z9hG4bK' + str(cseq)}\r\n"
        "From: <sip:bob@example.test>;tag=remote\r\n"
        "To: <sip:alice@127.0.0.1>;tag=local\r\n"
        "Call-ID: notify-sequence\r\n"
        f"CSeq: {cseq} {method}\r\nEvent: presence\r\n"
        + (f"Subscription-State: {state}\r\n" if state is not None else "")
        + "Content-Type: text/plain\r\n"
        f"Content-Length: {len(body.encode('utf8'))}\r\n\r\n" + body
    )
    return SIPMessage(headers.encode("utf8"))


def test_stale_notify_cannot_terminate_current_subscription(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    client.parse_message(notify(10))

    client.parse_message(
        notify(9, state="terminated;reason=timeout", body="old")
    )

    assert client.subscriptions.get(subscription.call_id) is subscription
    assert subscription.status == "active"
    assert subscription.last_notify_body == "available"
    assert len(events) == 1
    assert sent[-1].startswith("SIP/2.0 500 ")


def test_retransmitted_notify_acknowledges_without_repeating_callback(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    client.parse_message(notify(10))

    client.parse_message(
        notify(10, state="terminated;reason=timeout", body="changed retry")
    )

    assert client.subscriptions.get(subscription.call_id) is subscription
    assert subscription.status == "active"
    assert subscription.last_notify_body == "available"
    assert len(events) == 1
    assert len(sent) == 2
    assert all(response.startswith("SIP/2.0 200 ") for response in sent)


def test_same_notify_sequence_with_different_branch_is_rejected(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    client.parse_message(notify(10))

    client.parse_message(
        notify(10, branch="z9hG4bKdifferent", body="different transaction")
    )

    assert subscription.last_notify_body == "available"
    assert len(events) == 1
    assert sent[-1].startswith("SIP/2.0 500 ")


def test_older_completed_notify_retransmission_is_acknowledged(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    client.parse_message(notify(10))
    client.parse_message(notify(11, body="busy"))

    client.parse_message(notify(10, body="stale retry"))

    assert subscription.last_notify_body == "busy"
    assert len(events) == 2
    assert sent[-1].startswith("SIP/2.0 200 ")


def test_terminal_notify_retransmission_repeats_success_after_cleanup(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    terminal = notify(10, state="terminated;reason=timeout")
    client.parse_message(terminal)

    client.parse_message(terminal)

    assert subscription.call_id not in client.subscriptions
    assert len(events) == 1
    assert len(sent) == 2
    assert all(response.startswith("SIP/2.0 200 ") for response in sent)


@pytest.mark.parametrize(
    "cseq,method",
    [("invalid", "NOTIFY"), (-1, "NOTIFY"), (2**31, "NOTIFY"), (10, "INVITE")],
)
def test_invalid_notify_sequence_is_rejected_without_mutation(
    subscription_client, cseq, method
):
    client, subscription, sent, events = subscription_client

    client.parse_message(notify(cseq, method=method))

    assert subscription.status == "pending"
    assert subscription.last_notify_body == ""
    assert events == []
    assert sent[-1].startswith("SIP/2.0 400 ")


@pytest.mark.parametrize(
    "state", [None, "unknown", "active;expires=-1", "active;expires=invalid"]
)
def test_invalid_subscription_state_does_not_change_subscription(
    subscription_client, state
):
    client, subscription, sent, events = subscription_client

    client.parse_message(notify(state=state))

    assert subscription.status == "pending"
    assert subscription.expires == 3600
    assert subscription.last_notify_body == ""
    assert events == []
    assert sent[-1].startswith("SIP/2.0 400 ")


def test_newer_notify_can_update_and_terminate_subscription(
    subscription_client,
):
    client, subscription, sent, events = subscription_client
    client.parse_message(notify(10))
    client.parse_message(notify(11, body="busy", state="pending;expires=60"))

    assert subscription.status == "pending"
    assert subscription.expires == 60
    assert subscription.last_notify_body == "busy"
    assert len(events) == 2

    client.parse_message(notify(12, state="terminated;reason=timeout"))

    assert subscription.call_id not in client.subscriptions
    assert subscription.call_id not in client.tagLibrary
    assert len(events) == 3
    assert all(response.startswith("SIP/2.0 200 ") for response in sent)


def test_legacy_active_notify_without_expiry_is_accepted(subscription_client):
    client, subscription, sent, events = subscription_client

    client.parse_message(notify(state="active"))

    assert subscription.status == "active"
    assert subscription.expires == 3600
    assert len(events) == 1
    assert sent[-1].startswith("SIP/2.0 200 ")


def test_terminal_notify_ignores_expiry_parameter(subscription_client):
    client, subscription, sent, events = subscription_client

    client.parse_message(notify(state="terminated;expires=-1"))

    assert subscription.call_id not in client.subscriptions
    assert events[0]["expires"] == 3600
    assert sent[-1].startswith("SIP/2.0 200 ")


def test_notify_retry_after_failed_response_delivers_callback_once(
    subscription_client, monkeypatch
):
    client, subscription, sent, events = subscription_client
    send_response = client._send_request_response

    def fail_send(*args):
        raise OSError("temporary failure")

    monkeypatch.setattr(client, "_send_request_response", fail_send)
    with pytest.raises(OSError, match="temporary failure"):
        client.parse_message(notify(10))
    assert events == []

    monkeypatch.setattr(client, "_send_request_response", send_response)
    client.parse_message(notify(10))
    client.parse_message(notify(10))

    assert subscription.status == "active"
    assert len(events) == 1
    assert all(response.startswith("SIP/2.0 200 ") for response in sent)


def test_terminal_notify_replay_cache_expires(
    subscription_client, monkeypatch
):
    client, subscription, sent, events = subscription_client
    now = [0.0]
    monkeypatch.setattr(sip_module.time, "monotonic", lambda: now[0])
    terminal = notify(10, state="terminated;reason=timeout")
    client.parse_message(terminal)

    now[0] = 33.0
    client.parse_message(terminal)

    assert sent[-1].startswith("SIP/2.0 481 ")
    assert len(events) == 1
    assert client._notify_response_cache == {}


def test_notify_replay_cache_has_bounded_history(subscription_client):
    client, subscription, sent, events = subscription_client
    for sequence in range(1, 131):
        client.parse_message(notify(sequence))

    assert len(client._notify_response_cache) <= 128
    client.parse_message(notify(1, body="evicted stale notification"))

    assert sent[-1].startswith("SIP/2.0 500 ")
    assert subscription.last_notify_body == "available"
    assert len(events) == 130


@pytest.mark.parametrize("header", ["Via", "From", "To", "Call-ID", "CSeq"])
def test_notify_missing_mandatory_header_does_not_break_receiver(
    subscription_client, header
):
    client, subscription, sent, events = subscription_client
    raw = notify().raw
    lines = raw.split(b"\r\n")
    prefix = (header + ":").encode("ascii")
    request = SIPMessage(
        b"\r\n".join(line for line in lines if not line.startswith(prefix))
    )

    client.parse_message(request)

    assert subscription.status == "pending"
    assert subscription.last_notify_body == ""
    assert sent == []
    assert events == []
