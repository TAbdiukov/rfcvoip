from threading import Lock
from types import SimpleNamespace

import pyVoIP
from pyVoIP.SIP import RetryRequiredError, SIPClient
from pyVoIP.VoIP.status import PhoneStatus


def _client_with_deregister(monkeypatch, outcomes):
    client = object.__new__(SIPClient)
    client.recvLock = Lock()
    client.phone = SimpleNamespace(_status=None)
    calls = {"count": 0}

    def fake_deregister():
        calls["count"] += 1
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client._SIPClient__deregister = fake_deregister
    monkeypatch.setattr(pyVoIP, "REGISTER_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr("pyVoIP.SIP.time.sleep", lambda _: None)
    return client, calls


def test_deregister_retries_retry_required_without_recursion(monkeypatch):
    client, calls = _client_with_deregister(
        monkeypatch,
        [
            RetryRequiredError("500"),
            RetryRequiredError("500"),
            RetryRequiredError("500"),
        ],
    )

    assert client.deregister() is False
    assert calls["count"] == 3


def test_deregister_can_succeed_after_retry_required(monkeypatch):
    client, calls = _client_with_deregister(
        monkeypatch,
        [
            RetryRequiredError("500"),
            True,
        ],
    )

    assert client.deregister() is True
    assert calls["count"] == 2
    assert client.phone._status == PhoneStatus.INACTIVE

def test_deregister_preserves_exact_oserror_behavior(monkeypatch):
    client, calls = _client_with_deregister(
        monkeypatch,
        [
            OSError("socket closed"),
        ],
    )

    try:
        client.deregister()
    except OSError:
        pass
    else:
        raise AssertionError("Expected OSError to be re-raised")
    assert calls["count"] == 1
