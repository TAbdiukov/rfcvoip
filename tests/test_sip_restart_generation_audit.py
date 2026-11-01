from types import SimpleNamespace
from threading import Event, Thread

import pytest

import rfcvoip.SIP as sip_module
from rfcvoip import RTP
from rfcvoip.SIP import (
    RetryRequiredError,
    SIPClient,
    SIPMessage,
    SIPSubscription,
)
from rfcvoip.SIPTransport import SIPFramingError
from rfcvoip.VoIP.status import PhoneStatus


@pytest.fixture
def restartable_client(monkeypatch):
    connections = []
    threads = []

    class Socket:
        def gettimeout(self):
            return None

        def setblocking(self, value):
            pass

        def settimeout(self, value):
            pass

    class Connection:
        connection_oriented = True

        def __init__(self, *args, **kwargs):
            self.socket = Socket()
            self.closed = False
            connections.append(self)

        def open(self):
            pass

        def close(self):
            self.closed = True

        def recv_raw_message(self):
            raise AssertionError("The old receiver read from the new run")

    class PendingThread:
        def __init__(self, target, args=(), **kwargs):
            self.target = target
            self.args = args
            threads.append(self)

        def start(self):
            pass

    monkeypatch.setattr(sip_module, "SIPConnection", Connection)
    monkeypatch.setattr(sip_module, "Thread", PendingThread)
    monkeypatch.setattr(sip_module.time, "sleep", lambda delay: None)
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
        transport="tcp",
    )
    monkeypatch.setattr(client, "register", lambda: True)
    client.start()
    return client, connections, threads


def test_delayed_old_receive_worker_does_not_start_after_restart(
    restartable_client,
    monkeypatch,
):
    client, connections, threads = restartable_client
    calls = []
    monkeypatch.setattr(client, "recv_loop", lambda *args: calls.append(args))
    old_worker = threads[0]
    client.stop()
    client.start()

    old_worker.target(*old_worker.args)

    assert calls == []
    threads[-1].target(*threads[-1].args)
    assert len(calls) == 1
    assert connections[-1].closed is False


def test_running_old_receive_worker_cannot_read_the_restarted_connection(
    restartable_client,
):
    client, connections, _ = restartable_client
    old = connections[0]
    read_new = []

    def restart():
        client.stop()
        client.start()

        def new_receive():
            read_new.append(True)
            client.NSD = False
            return b"\r\n"

        connections[-1].recv_raw_message = new_receive
        return b"\r\n"

    old.recv_raw_message = restart

    client.recv_loop()

    assert read_new == []
    assert client.NSD is True
    assert client.connection is connections[-1]


@pytest.mark.parametrize("receive", ["recv", "_recv_message_before"])
@pytest.mark.parametrize("failure", [ConnectionError, SIPFramingError])
def test_old_receive_failure_cannot_close_the_restarted_connection(
    restartable_client,
    receive,
    failure,
):
    client, connections, _ = restartable_client
    old = connections[0]

    def fail_after_restart(*args, **kwargs):
        client.stop()
        client.start()
        raise failure("old connection failed")

    old.recv_raw_message = fail_after_restart
    old.recv_raw_message_before = fail_after_restart

    if receive == "recv":
        client.recv()
    else:
        client._recv_message_before(float("inf"))

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False


@pytest.mark.parametrize("fail_old_start", [False, True])
def test_old_start_completion_cannot_change_a_new_run(
    restartable_client,
    monkeypatch,
    fail_old_start,
):
    client, connections, threads = restartable_client
    client.stop()
    restarted = []

    def register():
        if not restarted:
            restarted.append(True)
            client.stop()
            client.start()
            if fail_old_start:
                raise RuntimeError("old startup failed")
        return True

    monkeypatch.setattr(client, "register", register)

    if fail_old_start:
        with pytest.raises(RuntimeError, match="old startup"):
            client.start()
    else:
        client.start()

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False
    # One worker for the original run and one for the restarted run.
    assert len(threads) == 2


def test_old_stop_completion_cannot_close_a_new_run(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    client.registerThread = object()

    def deregister():
        client._fail_signaling_connection()
        client.start()
        return True

    monkeypatch.setattr(client, "deregister", deregister)

    client.stop()

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False


def test_generation_guard_also_handles_reused_connection_identity(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    old = connections[0]
    monkeypatch.setattr(
        sip_module, "SIPConnection", lambda *args, **kwargs: old
    )
    old.open = lambda: setattr(old, "closed", False)

    def fail_after_reopen():
        client.stop()
        client.start()
        raise ConnectionError("old receive failed after reopen")

    old.recv_raw_message = fail_after_reopen

    client.recv()

    assert client.NSD is True
    assert client.connection is old
    assert old.closed is False


def test_old_delayed_registration_cannot_run_after_restart(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    registrations = []

    def wait(delay):
        client.stop()
        client.start()
        registrations.clear()
        return False

    event = SimpleNamespace(wait=wait, is_set=lambda: False)
    monkeypatch.setattr(client, "register", lambda: registrations.append(True))

    client._run_delayed_register(1.0, event)

    assert registrations == []
    assert client.NSD is True
    assert connections[-1].closed is False


def response(request):
    headers = (
        "SIP/2.0 200 OK\r\n"
        f"Call-ID: {request.headers['Call-ID']}\r\n"
        f"CSeq: {request.headers['CSeq']['check']} {request.method}\r\n"
        "Content-Length: 0\r\n\r\n"
    )
    return headers.encode()


def test_old_transaction_cannot_read_from_restarted_connection(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    old = connections[0]
    sent = []
    reads = []
    monkeypatch.setattr(
        client, "send_raw", lambda raw, target: sent.append(SIPMessage(raw))
    )

    def restart(deadline, **kwargs):
        client.stop()
        client.start()

        def read_new(*args, **kwargs):
            reads.append(True)
            return response(sent[0])

        connections[-1].recv_raw_message_before = read_new
        return None

    old.recv_raw_message_before = restart

    with pytest.raises(RuntimeError, match="stopped|restarted"):
        client.options("bob")

    assert reads == []
    assert client.NSD is True
    assert connections[-1].closed is False


@pytest.mark.parametrize("operation", ["register", "deregister"])
@pytest.mark.parametrize("outcome", [True, False, TimeoutError("old request")])
def test_old_registration_completion_cannot_change_new_run(
    restartable_client,
    monkeypatch,
    operation,
    outcome,
):
    client, connections, _ = restartable_client
    client.registerFailures = 2
    client.phone._status = PhoneStatus.REGISTERED

    def restart():
        client.stop()
        client.start()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client, "_SIPClient__" + operation, restart)
    method = getattr(SIPClient, operation).__get__(client, SIPClient)

    assert method() is False

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False
    assert client.registerFailures == 2
    assert client.phone._status is PhoneStatus.REGISTERED


def test_timed_out_invite_does_not_leave_dialog_tags(
    restartable_client,
    monkeypatch,
):
    client, _, _ = restartable_client
    client.invite_timeout = 0
    monkeypatch.setattr(client, "send_raw", lambda *args: None)
    original_tags = dict(client.tagLibrary)

    with pytest.raises(TimeoutError):
        client.invite(
            "bob",
            {40000: {0: RTP.PayloadType.PCMU}},
            RTP.TransmitType.SENDRECV,
        )

    assert client.tagLibrary == original_tags
    assert client.pending_invite_responses == {}


@pytest.mark.parametrize("shutdown", ["stop", "_fail_signaling_connection"])
def test_restart_during_old_close_keeps_new_connection_attached(
    restartable_client,
    shutdown,
):
    client, connections, _ = restartable_client
    old = connections[0]
    original_close = old.close

    def close_and_restart():
        original_close()
        client.start()

    old.close = close_and_restart

    getattr(client, shutdown)()

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False
    assert client.s is client.out is connections[-1].socket


@pytest.mark.parametrize("phase", ["constructor", "open"])
def test_stale_startup_completion_does_not_register_the_new_run_again(
    restartable_client,
    monkeypatch,
    phase,
):
    client, connections, threads = restartable_client
    client.stop()
    restarted = []
    registrations = []

    def restart_once():
        if not restarted:
            restarted.append(True)
            client.stop()
            client.start()

    connection_type = type(connections[0])
    if phase == "constructor":
        original_init = connection_type.__init__

        def construct(connection, *args, **kwargs):
            original_init(connection, *args, **kwargs)
            restart_once()

        monkeypatch.setattr(connection_type, "__init__", construct)
    else:
        monkeypatch.setattr(
            connection_type, "open", lambda connection: restart_once()
        )
    monkeypatch.setattr(client, "register", lambda: registrations.append(True))

    client.start()

    assert registrations == [True]
    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False
    assert len(threads) == 2


def test_old_registration_retry_delay_cannot_restart_registration_in_new_run(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    registrations = []

    def fail_register():
        raise RetryRequiredError("retry old request")

    def restart(delay):
        client.stop()
        client.start()

    monkeypatch.setattr(client, "_SIPClient__register", fail_register)
    monkeypatch.setattr(client, "register", lambda: registrations.append(True))
    monkeypatch.setattr(sip_module.time, "sleep", restart)

    assert SIPClient.register(client) is False

    assert registrations == [True]
    assert client.NSD is True
    assert connections[-1].closed is False


@pytest.mark.parametrize("phase", ["constructor", "open"])
def test_stale_startup_does_not_close_connection_reused_by_new_run(
    restartable_client,
    monkeypatch,
    phase,
):
    client, connections, _ = restartable_client
    reused = connections[0]
    client.stop()
    restarted = []

    def restart_once():
        if not restarted:
            restarted.append(True)
            client.stop()
            client.start()

    def factory(*args, **kwargs):
        if phase == "constructor":
            restart_once()
        return reused

    def open_connection():
        reused.closed = False
        if phase == "open":
            restart_once()

    reused.open = open_connection
    monkeypatch.setattr(sip_module, "SIPConnection", factory)

    client.start()

    assert client.NSD is True
    assert client.connection is reused
    assert reused.closed is False
    assert client.s is client.out is reused.socket


@pytest.mark.parametrize(
    "shutdown", ["stop", "_fail_signaling_connection", "startup-failure"]
)
@pytest.mark.parametrize(
    "helper_name", ["_clear_subscriptions", "_cancel_pending_register_thread"]
)
@pytest.mark.parametrize("reuse_connection", [False, True])
def test_retired_cleanup_helpers_cannot_touch_a_restarted_run(
    restartable_client,
    monkeypatch,
    shutdown,
    helper_name,
    reuse_connection,
):
    client, connections, _ = restartable_client
    if shutdown == "startup-failure":
        client.stop()
        failed = []

        def register():
            if not failed:
                failed.append(True)
                raise RuntimeError("old startup failed")
            return True

        monkeypatch.setattr(client, "register", register)
    old_event = Event()
    client._register_cancel_event = old_event
    old_subscription = SIPSubscription(
        "old-run",
        "bob",
        "sip:bob@127.0.0.1",
        "presence",
        [],
        "old-tag",
        status="active",
        subscription_state="active",
    )
    client.subscriptions["old-run"] = old_subscription
    client.tagLibrary["old-run"] = "old-tag"
    new_event = Event()
    new_subscription = SIPSubscription(
        "new-run",
        "bob",
        "sip:bob@127.0.0.1",
        "presence",
        [],
        "new-tag",
        status="active",
        subscription_state="active",
    )
    original = getattr(client, helper_name)
    restarted = []
    events = []

    def cleanup(*args, **kwargs):
        if not restarted:
            restarted.append(True)
            assert client.NSD is False
            assert client.connection is None
            if reuse_connection:
                reused = connections[-1]
                monkeypatch.setattr(
                    sip_module, "SIPConnection", lambda *args, **kwargs: reused
                )
                reused.open = lambda: setattr(reused, "closed", False)
            client.start()
            client._register_cancel_event = new_event
            client.subscriptions["new-run"] = new_subscription
            client.tagLibrary["new-run"] = "new-tag"
            client._notify_response_cache[("new-notify",)] = (0.0, "response")
            client._pending_subscription_callbacks.append(
                (events.append, {"run": "new"})
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(client, helper_name, cleanup)

    if shutdown == "startup-failure":
        with pytest.raises(RuntimeError, match="old startup"):
            client.start()
    else:
        getattr(client, shutdown)()

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False
    assert old_event.is_set()
    assert not new_event.is_set()
    assert client._register_cancel_event is new_event
    assert old_subscription.status == "terminated"
    assert client.subscriptions == {"new-run": new_subscription}
    assert client.tagLibrary["new-run"] == "new-tag"
    assert client._notify_response_cache == {
        ("new-notify",): (0.0, "response")
    }
    client._drain_subscription_callbacks()
    assert events == [{"run": "new"}]


def test_old_subscription_callback_can_restart_before_new_callback_delivery(
    restartable_client,
):
    client, connections, _ = restartable_client
    events = []
    item = SIPSubscription(
        "callback-old",
        "bob",
        "sip:bob@127.0.0.1",
        "presence",
        [],
        "local",
    )

    def callback(event):
        events.append(event["type"])
        if event["type"] == "old":
            client.stop()
            client.start()
            client._emit_subscription_event(item, event_type="new")

    client.subscription_callback = callback

    client._emit_subscription_event(item, event_type="old")

    assert events == ["old", "new"]
    assert client.NSD is True
    assert client.connection is connections[-1]
    assert not client._pending_subscription_callbacks
    assert client._subscription_callbacks_running is False


def test_retired_callback_queue_stops_before_cleanup_helpers_finish(
    restartable_client,
    monkeypatch,
):
    client, _, _ = restartable_client
    entered, release = Event(), Event()
    delivered = []

    def first(event):
        delivered.append(1)
        entered.set()
        assert release.wait(1)

    client._pending_subscription_callbacks.extend(
        [
            (first, {}),
            (lambda event: delivered.append(2), {}),
        ]
    )
    worker = Thread(target=client._drain_subscription_callbacks, daemon=True)
    worker.start()
    assert entered.wait(1)
    original_cancel = client._cancel_pending_register_thread

    def cancel(*args, **kwargs):
        release.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        return original_cancel(*args, **kwargs)

    monkeypatch.setattr(client, "_cancel_pending_register_thread", cancel)

    client.stop()

    assert delivered == [1]


@pytest.mark.parametrize("origin", ["start", "delayed-register"])
def test_internal_register_call_preserves_its_originating_generation(
    restartable_client,
    monkeypatch,
    origin,
):
    client, connections, _ = restartable_client
    if origin == "start":
        client.stop()
    original_register = SIPClient.register.__get__(client, SIPClient)
    registrations, restarted = [], []
    monkeypatch.setattr(
        client,
        "_SIPClient__register",
        lambda: registrations.append(True) or True,
    )

    def register():
        if not restarted:
            restarted.append(True)
            client.stop()
            client.start()
        return original_register()

    monkeypatch.setattr(client, "register", register)

    if origin == "start":
        client.start()
    else:
        client._run_delayed_register(0, Event())

    assert registrations == [True]
    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False


def test_internal_deregister_call_preserves_the_stopping_generation(
    restartable_client,
    monkeypatch,
):
    client, connections, _ = restartable_client
    client.registerThread = object()
    original_deregister = client.deregister
    deregistrations = []
    monkeypatch.setattr(
        client,
        "_SIPClient__deregister",
        lambda: deregistrations.append(True) or True,
    )

    def deregister():
        client.registerThread = None
        client.stop()
        client.start()
        return original_deregister()

    monkeypatch.setattr(client, "deregister", deregister)

    client.stop()

    assert deregistrations == []
    assert client.NSD is True
    assert client.connection is connections[-1]
    assert connections[-1].closed is False


@pytest.mark.parametrize("method", ["options", "invite"])
def test_request_builder_restart_cannot_send_old_request_on_new_run(
    restartable_client,
    monkeypatch,
    method,
):
    client, connections, _ = restartable_client
    builder_name = (
        "_build_options_request" if method == "options" else "gen_invite"
    )
    original = getattr(client, builder_name)
    sent = []
    original_tags = dict(client.tagLibrary)
    monkeypatch.setattr(client, "send_raw", lambda *args: sent.append(True))

    def build(*args, **kwargs):
        request = original(*args, **kwargs)
        client.stop()
        client.start()
        return request

    monkeypatch.setattr(client, builder_name, build)

    with pytest.raises(RuntimeError, match="stopped|restarted"):
        if method == "options":
            client.options("bob")
        else:
            client.invite(
                "bob",
                {40000: {0: RTP.PayloadType.PCMU}},
                RTP.TransmitType.SENDRECV,
            )

    assert sent == []
    assert client.tagLibrary == original_tags
    assert client.NSD is True
    assert client.connection is connections[-1]


@pytest.mark.parametrize(
    "phase", ["timer-entry", "cancel", "thread-construction"]
)
def test_old_registration_timer_scheduling_cannot_replace_new_run_timer(
    restartable_client,
    monkeypatch,
    phase,
):
    client, connections, _ = restartable_client
    restarted = []
    new_event = Event()
    new_thread = object()
    monkeypatch.setattr(client, "_SIPClient__register", lambda: True)

    def restart_once():
        if not restarted:
            restarted.append(True)
            client.stop()
            client.start()
            client._register_cancel_event = new_event
            client.registerThread = new_thread
            client.registerFailures = 2
            client.phone._status = PhoneStatus.REGISTERED

    if phase == "timer-entry":
        original = client._SIPClient__start_register_timer

        def timer(*args, **kwargs):
            restart_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(client, "_SIPClient__start_register_timer", timer)
    elif phase == "cancel":
        original = client._cancel_pending_register_thread

        def cancel(*args, **kwargs):
            restart_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(client, "_cancel_pending_register_thread", cancel)
    else:
        original = sip_module.Thread

        def thread(**kwargs):
            if kwargs["target"] == client._run_delayed_register:
                restart_once()
            return original(**kwargs)

        monkeypatch.setattr(sip_module, "Thread", thread)

    assert SIPClient.register(client) is False

    assert client.NSD is True
    assert client.connection is connections[-1]
    assert client._register_cancel_event is new_event
    assert client.registerThread is new_thread
    assert not new_event.is_set()
    assert client.registerFailures == 2
    assert client.phone._status is PhoneStatus.REGISTERED
