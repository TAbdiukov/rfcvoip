import ssl
import time
from types import SimpleNamespace

import pytest

import rfcvoip.SIPTransport as transport_module
from rfcvoip.SIP import SIPClient
from rfcvoip.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPTransport,
)


def connection(transport=SIPTransport.TCP):
    return SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, transport),
    )


@pytest.mark.parametrize("reopen", [False, True])
def test_close_during_receive_wait_preserves_original_socket_error(
    monkeypatch, reopen
):
    instance = connection()
    closed = []
    sock = SimpleNamespace(
        gettimeout=lambda: 1,
        setblocking=lambda value: None,
        settimeout=lambda value: None,
        close=lambda: closed.append(True),
    )
    instance.socket = sock
    original = OSError("socket closed during receive wait")
    restored_timeouts = []
    replacement = SimpleNamespace(settimeout=restored_timeouts.append)

    def wait(*args):
        instance.close()
        if reopen:
            instance.socket = replacement
        raise original

    monkeypatch.setattr(transport_module.select, "select", wait)

    with pytest.raises(OSError) as failure:
        instance.recv_raw_message_before(time.monotonic() + 1)

    assert failure.value is original
    assert closed == [True]
    assert restored_timeouts == []


@pytest.mark.parametrize("reopen", [False, True])
def test_close_during_partial_send_never_dereferences_detached_socket(reopen):
    instance = connection()
    closed = []
    forwarded = []
    replacement = SimpleNamespace(
        send=lambda data: forwarded.append(bytes(data)) or len(data)
    )

    def send(data):
        if closed:
            raise OSError("original socket is closed")
        instance.close()
        if reopen:
            instance.socket = replacement
        return 1

    instance.socket = SimpleNamespace(
        send=send, close=lambda: closed.append(True)
    )

    with pytest.raises(OSError, match="closed"):
        instance.send(b"abc")
    assert forwarded == []


@pytest.mark.parametrize("reopen", [False, True])
def test_close_during_partial_frame_never_dereferences_detached_socket(reopen):
    instance = connection()
    closed = []
    forwarded = []
    replacement = SimpleNamespace(
        recv=lambda size: forwarded.append(size)
        or b"Content-Length: 0\r\n\r\n"
    )

    def recv(size):
        if closed:
            raise OSError("original socket is closed")
        instance.close()
        if reopen:
            instance.socket = replacement
        return b"SIP/2.0 200 OK\r\n"

    instance.socket = SimpleNamespace(
        recv=recv, close=lambda: closed.append(True)
    )

    with pytest.raises(OSError, match="closed"):
        instance.recv_raw_message()
    assert instance._stream_buffer == b""
    assert forwarded == []


@pytest.mark.parametrize("with_deadline", [False, True])
def test_fatal_tls_read_error_triggers_signaling_cleanup(with_deadline):
    instance = connection(SIPTransport.TLS)
    closed = []

    def recv(size):
        raise ssl.SSLError(ssl.SSL_ERROR_SSL, "bad record mac")

    instance.socket = SimpleNamespace(
        recv=recv,
        gettimeout=lambda: 1,
        setblocking=lambda value: None,
        settimeout=lambda value: None,
        pending=lambda: 1,
        close=lambda: closed.append(True),
    )
    client = SIPClient(
        "127.0.0.1", 5060, "alice", "secret", phone=SimpleNamespace()
    )
    client.connection = instance
    client.s = client.out = instance.socket
    client.NSD = True
    failed = []
    client.fatalCallback = lambda: failed.append(True)

    if with_deadline:
        assert client._recv_message_before(time.monotonic() + 1) is None
    else:
        client.recv()

    assert client.NSD is False
    assert client.connection is None
    assert closed == [True]
    assert failed == [True]


@pytest.mark.parametrize(
    "error", [ssl.SSLWantReadError, ssl.SSLWantWriteError]
)
def test_retryable_tls_read_errors_leave_connection_open(error):
    instance = connection(SIPTransport.TLS)

    def recv(size):
        raise error()

    instance.socket = sock = SimpleNamespace(recv=recv)

    with pytest.raises(error):
        instance.recv_raw_message()

    assert instance.socket is sock
