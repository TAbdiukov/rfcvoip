from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from socket import socket
    from threading import Lock


@contextmanager
def acquired_lock_and_unblocked_socket(lock: "Lock", socket: "socket"):
    """Make a socket non-blocking while holding its corresponding lock.

    Restore its original timeout before releasing the lock, even on errors.
    """
    with lock:
        timeout = socket.gettimeout()
        try:
            socket.setblocking(False)
            yield
        finally:
            socket.settimeout(timeout)
