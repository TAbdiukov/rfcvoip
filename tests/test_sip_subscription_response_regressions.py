from types import SimpleNamespace

import pytest

from rfcvoip.SIP import SIPClient, SIPMessage, SIPSubscription


def client_subscription():
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
    )
    client.NSD = True
    subscription = SIPSubscription(
        call_id="response-sequence",
        target="bob",
        target_uri="sip:bob@127.0.0.1",
        event="presence",
        accept=["text/plain"],
        local_tag="local",
    )
    client.subscriptions[subscription.call_id] = subscription
    client.tagLibrary[subscription.call_id] = subscription.local_tag
    return client, subscription


def response(
    request,
    code=200,
    *,
    target="sip:bob@127.0.0.1:5070",
    expires=120,
    tag="remote",
):
    message = SIPMessage(request.encode())
    headers = (
        f"SIP/2.0 {code} Response\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKresponse\r\n"
        f"From: {message.headers['From']['raw']};tag={message.headers['From']['tag']}\r\n"
        f"To: <sip:bob@127.0.0.1>;tag={tag}\r\n"
        f"Call-ID: {message.headers['Call-ID']}\r\n"
        f"CSeq: {message.headers['CSeq']['check']} SUBSCRIBE\r\n"
        f"Contact: <{target}>\r\nExpires: {expires}\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    return SIPMessage(headers.encode())


def test_stale_subscribe_response_cannot_roll_back_latest_dialog():
    client, subscription = client_subscription()
    first = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(response(first))
    second = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(
        response(second, target="sip:bob@127.0.0.1:5080", expires=60)
    )
    current = subscription.snapshot()

    client.parse_message(response(first))

    assert subscription.snapshot() == current


def test_late_provisional_subscribe_response_cannot_replace_final_state():
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(response(request))
    current = subscription.snapshot()

    client.parse_message(
        response(request, 100, target="sip:early@127.0.0.1", expires=999)
    )

    assert subscription.snapshot() == current


def test_successful_subscribe_response_without_remote_tag_is_ignored():
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)
    current = subscription.snapshot()
    incomplete = response(request)
    incomplete.headers["To"]["tag"] = ""

    client.parse_message(incomplete)

    assert subscription.snapshot() == current


def test_duplicate_subscribe_response_cannot_undo_newer_notify(monkeypatch):
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)
    accepted = response(request)
    client.parse_message(accepted)
    notify = SIPMessage(
        b"NOTIFY sip:alice@127.0.0.1 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5070;branch=z9hG4bKnotify\r\n"
        b"From: <sip:bob@127.0.0.1>;tag=remote\r\n"
        b"To: <sip:alice@127.0.0.1>;tag=local\r\n"
        b"Call-ID: response-sequence\r\nCSeq: 2 NOTIFY\r\n"
        b"Event: presence\r\nSubscription-State: active;expires=60\r\n"
        b"Contact: <sip:bob@127.0.0.1:5080>\r\nContent-Length: 0\r\n\r\n"
    )
    monkeypatch.setattr(client, "_send_request_response", lambda *args: None)
    client.parse_message(notify)
    current = subscription.snapshot()

    client.parse_message(accepted)

    assert subscription.snapshot() == current


@pytest.mark.parametrize("wrong_header", ["From", "To"])
def test_response_for_different_subscription_dialog_is_ignored(wrong_header):
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(response(request))
    current = subscription.snapshot()
    unrelated = response(request, target="sip:other@127.0.0.1", expires=999)
    unrelated.headers[wrong_header]["tag"] = "other-dialog"

    client.parse_message(unrelated)

    assert subscription.snapshot() == current


def test_failed_subscription_refresh_cannot_change_remote_target_or_expiry():
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(response(request))
    subscription.subscription_state = subscription.status = "active"
    refresh = client._build_subscribe_request(subscription, expires=3600)

    client.parse_message(
        response(refresh, 503, target="sip:error@127.0.0.1", expires=0)
    )

    assert subscription.status == "active"
    assert subscription.remote_target == "sip:bob@127.0.0.1:5070"
    assert subscription.expires == 120
    assert subscription.last_response_code == 503


def test_stale_481_during_unsubscribe_cannot_remove_current_subscription(
    monkeypatch,
):
    client, subscription = client_subscription()
    initial = client._build_subscribe_request(subscription, expires=3600)
    client.parse_message(response(initial))
    stale = response(initial, 481)
    requests = []
    monkeypatch.setattr(
        client, "send_raw", lambda raw, target: requests.append(raw.decode())
    )
    reads = [stale]

    def receive(deadline):
        if reads:
            return reads.pop()
        return response(requests[-1], expires=0)

    monkeypatch.setattr(client, "_recv_message_before", receive)

    result = client.unsubscribe_from(subscription.call_id)

    assert client.subscriptions.get(subscription.call_id) is subscription
    assert subscription.status == result["status"] == "cancelling"
    assert subscription.last_response_code == 200


@pytest.mark.parametrize("expires", [-1, "invalid", "+10"])
def test_invalid_successful_subscription_expiry_preserves_requested_value(
    expires,
):
    client, subscription = client_subscription()
    request = client._build_subscribe_request(subscription, expires=3600)

    client.parse_message(response(request, expires=expires))

    assert subscription.expires == 3600
