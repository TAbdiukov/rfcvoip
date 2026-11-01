import socket
import time

import pytest

from rfcvoip.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPFramingError,
    SIPTransport,
)


@pytest.mark.parametrize("with_deadline", [False, True])
def test_udp_receive_preserves_large_sip_datagrams(with_deadline):
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.UDP),
    )
    body = b"x" * 12000
    message = (
        b"SIP/2.0 200 OK\r\nContent-Type: text/plain\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    try:
        connection.open()
        connection.socket.settimeout(1)
        sender.sendto(message, connection.socket.getsockname())

        if with_deadline:
            received = connection.recv_raw_message_before(time.monotonic() + 1)
        else:
            received = connection.recv_raw_message()

        assert received == message
        assert connection.socket.gettimeout() == 1
    finally:
        connection.close()
        sender.close()


@pytest.mark.parametrize(
    "stale_data",
    [
        b"SIP/2.0 500 Old Response\r\nContent-Length: 0\r\n\r\n",
        b"SIP/2.0 500 Old",
    ],
)
def test_reopened_stream_does_not_replay_previous_connection_bytes(
    monkeypatch, stale_data
):
    left, right = socket.socketpair()
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    fresh_message = b"SIP/2.0 200 Fresh\r\nContent-Length: 0\r\n\r\n"
    connection._stream_buffer = stale_data
    connection.last_recv_address = ("192.0.2.1", 5060)
    monkeypatch.setattr(connection, "_open_stream_socket", lambda: left)
    try:
        connection.close()
        connection.open()
        left.settimeout(1)
        right.sendall(fresh_message)

        assert connection.recv_raw_message() == fresh_message
        assert connection.last_recv_address is None
    finally:
        connection.close()
        right.close()


def test_stream_rejects_missing_content_length_before_consuming_frame():
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    connection._stream_buffer = (
        b"SIP/2.0 200 OK\r\nContent-Type: text/plain\r\n\r\nbody"
        b"SIP/2.0 500 Next\r\nContent-Length: 0\r\n\r\n"
    )

    with pytest.raises(SIPFramingError, match="Content-Length"):
        connection._pop_stream_message()


def test_stream_eof_signals_a_permanent_connection_failure():
    left, right = socket.socketpair()
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    connection.socket = left
    left.settimeout(1)
    right.close()
    try:
        with pytest.raises(ConnectionError, match="socket closed"):
            connection.recv_raw_message_before(time.monotonic() + 1)
    finally:
        connection.close()
