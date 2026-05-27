import socket
from threading import Lock

import pytest

from rfcvoip.util import acquired_lock_and_unblocked_socket


def test_acquired_lock_and_unblocked_socket_acquires_lock_and_unblocks_socket():
    lock = Lock()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5.0)

        assert not lock.locked()
        assert sock.gettimeout() == 5.0

        with acquired_lock_and_unblocked_socket(lock, sock):
            assert lock.locked()
            assert lock.acquire(blocking=False) is False
            assert sock.gettimeout() == 0.0

        assert not lock.locked()
        assert sock.gettimeout() == 5.0


def test_acquired_lock_and_unblocked_socket_restores_state_after_exception():
    lock = Lock()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2.5)

        with pytest.raises(RuntimeError, match="boom"):
            with acquired_lock_and_unblocked_socket(lock, sock):
                assert lock.locked()
                assert sock.gettimeout() == 0.0
                raise RuntimeError("boom")

        assert not lock.locked()
        assert sock.gettimeout() == 2.5


def test_acquired_lock_and_unblocked_socket_restores_blocking_socket_timeout():
    lock = Lock()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        assert sock.gettimeout() is None

        with acquired_lock_and_unblocked_socket(lock, sock):
            assert lock.locked()
            assert sock.gettimeout() == 0.0

        assert not lock.locked()
        assert sock.gettimeout() is None