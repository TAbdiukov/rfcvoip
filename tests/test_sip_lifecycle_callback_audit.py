from types import SimpleNamespace
from threading import Event, Thread

import pytest

import rfcvoip
from rfcvoip import RTP
from rfcvoip.SIP import SIPClient, SIPMessage, SIPStatus, SIPSubscription
from rfcvoip.VoIP.status import PhoneStatus


def client():
    instance = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(calls={}),
        myIP="127.0.0.1",
    )
    instance.NSD = True
    return instance


def response(request, status=200):
    lines = (
        f"SIP/2.0 {status} Response\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:5060;branch={request.headers['Via'][0]['branch']}\r\n"
        f"From: {request.headers['From']['raw']};tag={request.headers['From']['tag']}\r\n"
        f"To: {request.headers['To']['raw']};tag=remote\r\n"
        f"Call-ID: {request.headers['Call-ID']}\r\n"
        f"CSeq: {request.headers['CSeq']['check']} {request.method}\r\n"
        "Contact: <sip:bob@127.0.0.1:5070>\r\n"
    )
    if status in (401, 407):
        header = "WWW-Authenticate" if status == 401 else "Proxy-Authenticate"
        lines += (
            f'{header}: Digest realm="test", nonce="nonce", qop="auth"\r\n'
        )
    return SIPMessage((lines + "Content-Length: 0\r\n\r\n").encode())


def request(method="INVITE"):
    return SIPMessage(
        (
            f"{method} sip:alice@127.0.0.1 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1:5070;branch=z9hG4bKcallback\r\n"
            "From: <sip:bob@127.0.0.1>;tag=remote\r\n"
            "To: <sip:alice@127.0.0.1>;tag=local\r\n"
            f"Call-ID: callback-failure\r\nCSeq: 1 {method}\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode()
    )


@pytest.mark.parametrize("status", [401, 407])
def test_old_invite_challenge_retransmission_replays_ack_only(
    monkeypatch, status
):
    instance = client()
    sent = []
    dispatched = []
    instance.callCallback = dispatched.append
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    reads = []

    def receive(deadline):
        requests = [item for item in sent if item.method == "INVITE"]
        if not reads:
            challenge = response(requests[0], status)
            reads.append(challenge)
            return challenge
        if len(reads) == 1:
            reads.append(reads[0])
            return reads[0]
        return response(requests[-1])

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    invite, call_id, _ = instance.invite(
        "bob",
        {40000: {0: RTP.PayloadType.PCMU}},
        RTP.TransmitType.SENDRECV,
    )

    assert dispatched == []
    assert [item.method for item in sent] == ["INVITE", "ACK", "INVITE", "ACK"]
    assert sent[1].raw == sent[3].raw
    assert invite.headers["CSeq"]["check"] == "2"
    assert int(instance.pop_pending_invite_response(call_id).status) == 200


@pytest.mark.parametrize("method", ["INVITE", "BYE", "CANCEL"])
def test_request_callback_failure_cannot_abort_unrelated_transaction(
    monkeypatch, method
):
    instance = client()
    instance.phone.calls["callback-failure"] = object()
    callbacks = []

    def fail_callback(message):
        callbacks.append(message.method)
        raise RuntimeError("application callback failed")

    instance.callCallback = fail_callback
    sent = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    received = [request(method)]

    def receive(deadline):
        if received:
            return received.pop()
        return response(sent[0])

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    answer = instance.options("bob")

    assert int(answer.status) == 200
    assert callbacks == [method]
    assert len(sent) == 2
    assert int(sent[1].status) == (500 if method == "INVITE" else 200)


@pytest.mark.parametrize("first_status", [180, 200, 486])
def test_callback_failure_preserves_an_already_sent_final_response(
    monkeypatch,
    first_status,
):
    instance = client()
    sent = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )

    def callback(message):
        instance.send_response(
            message, instance.gen_response(message, SIPStatus(first_status))
        )
        raise RuntimeError("application failed after answering")

    instance.callCallback = callback

    instance.parse_message(request())

    assert [int(item.status) for item in sent] == (
        [first_status] if first_status >= 200 else [first_status, 500]
    )


def subscription(instance):
    item = SIPSubscription(
        call_id="shutdown-subscription",
        target="bob",
        target_uri="sip:bob@127.0.0.1",
        event="presence",
        accept=["text/plain"],
        local_tag="local",
        remote_tag="remote",
        status="active",
        subscription_state="active",
    )
    instance.subscriptions[item.call_id] = item
    instance.tagLibrary[item.call_id] = item.local_tag
    return item


@pytest.mark.parametrize("shutdown", ["stop", "_fail_signaling_connection"])
def test_shutdown_discards_subscription_dialogs_and_replay_cache(shutdown):
    instance = client()
    item = subscription(instance)
    instance._notify_response_cache[("completed-notify",)] = (0.0, "response")

    getattr(instance, shutdown)()

    assert instance.list_subscriptions() == []
    assert item.call_id not in instance.tagLibrary
    assert instance._notify_response_cache == {}
    assert item.status == item.subscription_state == "terminated"


@pytest.mark.parametrize("shutdown", ["stop", "_fail_signaling_connection"])
def test_shutdown_discards_queued_subscription_events(shutdown):
    instance = client()
    item = subscription(instance)
    events = []
    instance.subscription_callback = events.append
    instance._send_request_response = lambda *args: None
    with instance.recvLock:
        instance.parse_message(notify(item))
        assert len(instance._pending_subscription_callbacks) == 1
        getattr(instance, shutdown)()

    instance._drain_subscription_callbacks()

    assert not instance._pending_subscription_callbacks
    assert events == []


def notify(item, sequence=1):
    return SIPMessage(
        (
            "NOTIFY sip:alice@127.0.0.1 SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP 127.0.0.1:5070;branch=z9hG4bKnotify{sequence}\r\n"
            "From: <sip:bob@127.0.0.1>;tag=remote\r\n"
            f"To: <sip:alice@127.0.0.1>;tag={item.local_tag}\r\n"
            f"Call-ID: {item.call_id}\r\nCSeq: {sequence} NOTIFY\r\n"
            f"Event: presence\r\nSubscription-State: active;expires={120 * sequence}\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode()
    )


@pytest.mark.parametrize(
    "operation",
    [
        "options",
        "subscribe",
        "unsubscribe",
        "register",
        "deregister",
        "invite",
        "receive",
    ],
)
def test_subscription_callback_can_start_transaction_after_receive_unlocks(
    monkeypatch,
    operation,
):
    instance = client()
    if operation != "subscribe":
        subscription(instance)
    sent = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    monkeypatch.setattr(
        instance, "_SIPClient__start_register_timer", lambda **kwargs: None
    )
    lock_states = []
    nested = []

    def callback(event):
        lock_states.append(instance.recvLock.locked())
        # Avoid hanging the regression against the old implementation.
        # Once unlocked, exercise the actual reentrant request path.
        if not lock_states[-1]:
            nested.append(instance.options("nested"))
        if operation == "receive":
            instance.NSD = False

    instance.subscription_callback = callback
    reads = []

    def receive(deadline):
        if not reads:
            reads.append(True)
            return notify(next(iter(instance.subscriptions.values())))
        request = next(
            item for item in reversed(sent) if getattr(item, "method", None)
        )
        return response(request)

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    if operation == "options":
        instance.options("bob")
    elif operation == "subscribe":
        instance.subscribe_to("bob")
    elif operation == "unsubscribe":
        instance.unsubscribe_from("shutdown-subscription")
    elif operation == "register":
        assert instance.register() is True
    elif operation == "deregister":
        assert instance.deregister() is True
    elif operation == "invite":
        instance.invite(
            "bob",
            {40000: {0: RTP.PayloadType.PCMU}},
            RTP.TransmitType.SENDRECV,
        )
    else:
        reads.append(True)
        instance.s = SimpleNamespace(
            gettimeout=lambda: None,
            setblocking=lambda value: None,
            settimeout=lambda value: None,
        )
        instance.connection = SimpleNamespace(
            recv_raw_message=lambda: notify(
                next(iter(instance.subscriptions.values()))
            ).raw,
        )
        instance.recv_loop()

    assert lock_states == [False]
    assert len(nested) == 1
    assert int(nested[0].status) == 200
    assert len(
        [item for item in sent if getattr(item, "method", None) == "OPTIONS"]
    ) == 1 + (operation == "options")


def test_queued_subscription_callbacks_keep_order_without_recursive_delivery(
    monkeypatch,
):
    instance = client()
    item = subscription(instance)
    sent = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    notifications = [notify(item, 2), notify(item, 1)]
    callbacks = []
    depth = [0]

    def callback(event):
        depth[0] += 1
        callbacks.append((event["expires"], depth[0]))
        instance.options("nested")
        depth[0] -= 1

    instance.subscription_callback = callback

    def receive(deadline):
        if notifications:
            return notifications.pop()
        request = next(
            item for item in reversed(sent) if getattr(item, "method", None)
        )
        return response(request)

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    instance.options("outer")

    assert callbacks == [(120, 1), (240, 1)]
    assert not instance._pending_subscription_callbacks


def test_call_callback_nested_transaction_fails_fast_and_outer_continues(
    monkeypatch,
):
    instance = client()
    sent = []
    errors = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    incoming = [request()]

    def callback(message):
        try:
            instance.options("nested")
        except RuntimeError as error:
            errors.append(str(error))
        instance.send_response(message, instance.gen_busy(message))

    instance.callCallback = callback

    def receive(deadline):
        if incoming:
            return incoming.pop()
        return response(
            next(
                item
                for item in reversed(sent)
                if getattr(item, "method", None)
            )
        )

    monkeypatch.setattr(instance, "_recv_message_before", receive)
    # A guard must run before attempting to reacquire the nonreentrant lock.
    # Run in a daemon thread so a regression reports failure instead of hanging.
    answers = []
    worker = Thread(
        target=lambda: answers.append(instance.options("outer")), daemon=True
    )
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert "callCallback" in errors[0]
    assert int(answers[0].status) == 200
    assert int(instance.options("normal").status) == 200


def test_call_callback_can_queue_transaction_on_another_thread(monkeypatch):
    instance = client()
    sent = []
    answers = []
    threads = []
    ready = Event()
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    incoming = [request()]

    def nested():
        ready.set()
        answers.append(instance.options("worker"))

    def callback(message):
        worker = Thread(target=nested, daemon=True)
        threads.append(worker)
        worker.start()
        assert ready.wait(1)
        instance.send_response(message, instance.gen_busy(message))

    instance.callCallback = callback

    def receive(deadline):
        if incoming:
            return incoming.pop()
        return response(
            next(
                item
                for item in reversed(sent)
                if getattr(item, "method", None)
            )
        )

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    assert int(instance.options("outer").status) == 200
    threads[0].join(timeout=1)

    assert not threads[0].is_alive()
    assert len(answers) == 1
    assert int(answers[0].status) == 200


def test_call_callback_outside_receive_lock_can_start_transaction(monkeypatch):
    instance = client()
    sent = []
    answers = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    monkeypatch.setattr(
        instance, "_recv_message_before", lambda deadline: response(sent[-1])
    )

    def callback(message):
        answers.append(instance.options("nested"))
        instance.send_response(message, instance.gen_busy(message))

    instance.callCallback = callback

    instance.parse_message(request())

    assert len(answers) == 1
    assert int(answers[0].status) == 200


@pytest.mark.parametrize("operation", ["register", "invite"])
def test_callback_reentry_guard_runs_before_transaction_state_changes(
    monkeypatch,
    operation,
):
    instance = client()
    instance.registerFailures = rfcvoip.REGISTER_FAILURE_THRESHOLD - 1
    instance.phone._status = PhoneStatus.REGISTERED
    sent = []
    errors = []
    states = []
    monkeypatch.setattr(
        instance, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )
    incoming = [request()]

    def snapshot():
        return (
            dict(instance.tagLibrary),
            list(instance.tags),
            instance.callID.current(),
            instance.inviteCounter.current(),
            instance.sessID.current(),
            instance.registerFailures,
            instance.phone._status,
            instance.registerThread,
            instance._register_cancel_event,
            instance.NSD,
        )

    def callback(message):
        states.append(snapshot())
        try:
            if operation == "register":
                instance.register()
            else:
                instance.invite(
                    "nested",
                    {40000: {0: RTP.PayloadType.PCMU}},
                    RTP.TransmitType.SENDRECV,
                )
        except RuntimeError as error:
            errors.append(str(error))
        states.append(snapshot())
        instance.send_response(message, instance.gen_busy(message))

    instance.callCallback = callback

    def receive(deadline):
        if incoming:
            return incoming.pop()
        return response(
            next(
                item
                for item in reversed(sent)
                if getattr(item, "method", None)
            )
        )

    monkeypatch.setattr(instance, "_recv_message_before", receive)

    assert int(instance.options("outer").status) == 200

    assert len(errors) == 1
    assert "callCallback" in errors[0]
    assert states[0] == states[1]
