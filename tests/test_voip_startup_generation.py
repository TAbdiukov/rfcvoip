from types import SimpleNamespace

import pytest

import rfcvoip.SIP as sip_module
from rfcvoip.VoIP import CallState, PhoneStatus, VoIPPhone


@pytest.fixture
def startup_phone(monkeypatch):
    connections = []
    workers = []
    hooks = []

    class Connection:
        def __init__(self, *args, **kwargs):
            self.socket = SimpleNamespace()
            self.closed = False
            connections.append(self)

        def open(self):
            if hooks:
                hooks[0](self)

        def close(self):
            self.closed = True

    class PendingThread:
        def __init__(self, **kwargs):
            workers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(sip_module, "SIPConnection", Connection)
    monkeypatch.setattr(sip_module, "Thread", PendingThread)
    phone = VoIPPhone("127.0.0.1", 5060, "alice", "secret", myIP="127.0.0.1")

    def register():
        phone._status = PhoneStatus.REGISTERED
        return True

    monkeypatch.setattr(phone.sip, "register", register)
    return phone, connections, workers, hooks


@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.parametrize("stop_layer", ["sip", "phone"])
def test_stale_phone_start_completion_preserves_a_newer_run(
    startup_phone, old_failure, stop_layer
):
    phone, connections, workers, hooks = startup_phone

    def restart(connection):
        if len(connections) == 1:
            if stop_layer == "sip":
                phone.sip.stop()
            else:
                phone.stop()
            phone.start()
            if old_failure:
                raise RuntimeError("old startup failed after restart")

    hooks.append(restart)

    if old_failure:
        with pytest.raises(RuntimeError, match="old startup failed"):
            phone.start()
    else:
        phone.start()

    assert phone.NSD is phone.sip.NSD is True
    assert phone.sip.connection is connections[-1]
    assert connections[-1].closed is False
    assert phone.get_status() is PhoneStatus.REGISTERED
    assert len(workers) == 1


def test_overlapping_phone_start_does_not_stop_startup_sip_connection(
    startup_phone,
):
    phone, connections, workers, hooks = startup_phone

    def overlap(connection):
        with pytest.raises(RuntimeError, match="already"):
            phone.start()

    hooks.append(overlap)

    phone.start()

    assert phone.NSD is phone.sip.NSD is True
    assert connections[0].closed is False
    assert len(connections) == len(workers) == 1


def test_overlapping_phone_start_before_sip_start_is_declined(
    startup_phone, monkeypatch
):
    phone, connections, workers, _hooks = startup_phone
    start = phone.sip.start
    entered = []

    def overlap():
        if not entered:
            entered.append(True)
            with pytest.raises(RuntimeError, match="already"):
                phone.start()
        start()

    monkeypatch.setattr(phone.sip, "start", overlap)

    phone.start()

    assert phone.NSD is phone.sip.NSD is True
    assert len(connections) == len(workers) == 1
    assert connections[0].closed is False


@pytest.mark.parametrize("old_failure", [False, True])
def test_phone_stop_invalidates_incomplete_startup(startup_phone, old_failure):
    phone, connections, workers, hooks = startup_phone

    def stop(connection):
        phone.stop()
        if old_failure:
            raise RuntimeError("stopped startup failed")

    hooks.append(stop)

    if old_failure:
        with pytest.raises(RuntimeError, match="stopped startup failed"):
            phone.start()
    else:
        phone.start()

    assert phone.NSD is phone.sip.NSD is False
    assert phone.get_status() is PhoneStatus.INACTIVE
    assert connections[0].closed is True
    assert workers == []


def test_owned_startup_failure_allows_a_clean_retry(startup_phone):
    phone, connections, workers, hooks = startup_phone

    def fail(connection):
        raise OSError("connection setup failed")

    hooks.append(fail)

    with pytest.raises(OSError, match="connection setup failed"):
        phone.start()

    assert phone.NSD is phone.sip.NSD is False
    assert phone.get_status() is PhoneStatus.FAILED
    assert connections[0].closed is True
    hooks.clear()

    phone.start()

    assert phone.NSD is phone.sip.NSD is True
    assert phone.sip.connection is connections[-1]
    assert connections[-1].closed is False
    assert len(workers) == 1


@pytest.mark.parametrize("phase", ["call_cleanup", "socket_close"])
def test_stale_phone_stop_preserves_independent_sip_restart(
    startup_phone, phase
):
    phone, connections, _workers, _hooks = startup_phone
    phone.start()
    old = connections[0]
    if phase == "call_cleanup":

        def restart():
            phone.sip.stop()
            phone.sip.start()

        phone.calls["old"] = SimpleNamespace(
            call_id="old", state=CallState.ANSWERED, hangup=restart
        )
    else:
        close = old.close

        def restart_during_close():
            close()
            phone.sip.start()

        old.close = restart_during_close

    phone.stop()

    assert phone.NSD is phone.sip.NSD is True
    assert phone.sip.connection is connections[-1]
    assert connections[-1].closed is False
    assert phone.get_status() is PhoneStatus.REGISTERED


def test_phone_stop_tracks_independent_sip_shutdown_during_call_cleanup(
    startup_phone,
):
    phone, connections, _workers, _hooks = startup_phone
    phone.start()
    phone.calls["old"] = SimpleNamespace(
        call_id="old", state=CallState.ANSWERED, hangup=phone.sip.stop
    )

    phone.stop()

    assert phone.NSD is phone.sip.NSD is False
    assert phone.get_status() is PhoneStatus.INACTIVE
    assert connections[0].closed is True


def test_restart_between_phone_stop_check_and_sip_stop_entry_is_preserved(
    startup_phone, monkeypatch
):
    phone, connections, _workers, _hooks = startup_phone
    phone.start()
    stop = phone.sip.stop
    entered = []

    def restart_before_stop(*args, **kwargs):
        if not entered:
            entered.append(True)
            stop()
            phone.sip.start()
        return stop(*args, **kwargs)

    monkeypatch.setattr(phone.sip, "stop", restart_before_stop)

    phone.stop()

    assert phone.NSD is phone.sip.NSD is True
    assert phone.sip.connection is connections[-1]
    assert connections[-1].closed is False
    assert phone.get_status() is PhoneStatus.REGISTERED
