import socket
import time
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import rfcvoip
from rfcvoip.SIP import SIPClient, SIPMessage, SIPParseError
from rfcvoip.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPTransport,
)
from rfcvoip.VoIP import VoIPPhone
from rfcvoip.VoIP.status import PhoneStatus


@pytest.mark.parametrize("receive_mode", ["background", "transaction"])
@pytest.mark.parametrize("failure", ["framing", "missing-length", "eof"])
def test_unrecoverable_stream_failure_stops_signaling(
    receive_mode,
    failure,
):
    left, right = socket.socketpair()
    phone = SimpleNamespace(_status=PhoneStatus.REGISTERED)
    failed = []
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=phone,
        myIP="127.0.0.1",
        fatalCallback=lambda: failed.append(True),
    )
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    connection.socket = left
    client.connection = connection
    client.s = client.out = left
    client.NSD = True
    cancelled = Event()
    client._register_cancel_event = cancelled
    left.settimeout(1)
    try:
        if failure == "framing":
            right.sendall(b"SIP/2.0 200 OK\r\nContent-Length: invalid\r\n\r\n")
        elif failure == "missing-length":
            right.sendall(
                b"SIP/2.0 200 OK\r\nVia: SIP/2.0/TCP 127.0.0.1\r\n\r\n"
            )
        else:
            right.close()

        if receive_mode == "background":
            client.recv()
        else:
            assert client._recv_message_before(time.monotonic() + 1) is None

        assert client.NSD is False
        assert client.connection is None
        assert client.s is None
        assert client.out is None
        assert left.fileno() == -1
        assert cancelled.is_set()
        assert phone._status is PhoneStatus.FAILED
        assert failed == [True]
        client.recv()
        assert failed == [True]
    finally:
        left.close()
        right.close()


def test_stream_failure_cleans_up_phone_without_deregister_deadlock():
    left, right = socket.socketpair()
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
    )
    client = phone.sip
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    connection.socket = left
    client.connection = connection
    client.s = client.out = left
    client.NSD = phone.NSD = True
    left.settimeout(1)
    try:
        right.sendall(b"SIP/2.0 200 OK\r\nContent-Length: bad\r\n\r\n")
        # Receive processing already owns this lock. Fatal cleanup must not
        # attempt a synchronous deregistration which takes it a second time.
        with client.recvLock:
            client.recv()

        assert client.NSD is False
        assert phone.NSD is False
        assert phone.get_status() is PhoneStatus.FAILED
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("failure", ["framing", "eof"])
def test_stream_failure_during_register_notifies_fatal_only_once(failure):
    left, right = socket.socketpair()
    phone = SimpleNamespace(_status=PhoneStatus.REGISTERED)
    failed = []
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=phone,
        myIP="127.0.0.1",
        transport="tcp",
        fatalCallback=lambda: failed.append(True),
    )
    connection = SIPConnection(
        "127.0.0.1",
        0,
        ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP),
    )
    connection.socket = left
    client.connection = connection
    client.s = client.out = left
    client.NSD = True
    client.registerFailures = rfcvoip.REGISTER_FAILURE_THRESHOLD - 1
    left.settimeout(1)
    try:
        if failure == "framing":
            right.sendall(b"SIP/2.0 200 OK\r\nContent-Length: bad\r\n\r\n")
        else:
            # Allow REGISTER writes while presenting a real EOF on reads.
            right.shutdown(socket.SHUT_WR)

        assert client.register() is False

        assert client.NSD is False
        assert client.connection is None
        assert (
            client.registerFailures == rfcvoip.REGISTER_FAILURE_THRESHOLD - 1
        )
        assert phone._status is PhoneStatus.FAILED
        assert failed == [True]
        assert client.registerThread is None
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("result", [True, False, TimeoutError("stopped")])
def test_registration_result_after_shutdown_preserves_stopped_status(
    monkeypatch,
    result,
):
    phone = SimpleNamespace(_status=PhoneStatus.REGISTERED)
    failed = []
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=phone,
        myIP="127.0.0.1",
        fatalCallback=lambda: failed.append(True),
    )
    client.NSD = True
    client.registerFailures = rfcvoip.REGISTER_FAILURE_THRESHOLD - 1

    def stop_during_register():
        client.NSD = False
        phone._status = PhoneStatus.INACTIVE
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client, "_SIPClient__register", stop_during_register)

    assert client.register() is False

    assert client.registerFailures == rfcvoip.REGISTER_FAILURE_THRESHOLD - 1
    assert phone._status is PhoneStatus.INACTIVE
    assert failed == []
    assert client.registerThread is None


def test_queued_registration_does_not_restart_stopped_signaling(monkeypatch):
    phone = SimpleNamespace(_status=PhoneStatus.REGISTERED)
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=phone,
        myIP="127.0.0.1",
    )
    client.NSD = True
    calls = []
    monkeypatch.setattr(
        client,
        "_SIPClient__register",
        lambda: calls.append(True) or True,
    )
    result = []
    with client.recvLock:
        worker = Thread(
            target=lambda: result.append(client.register()), daemon=True
        )
        worker.start()
        client.NSD = False
        phone._status = PhoneStatus.FAILED
    worker.join(timeout=1)

    assert worker.is_alive() is False
    assert calls == []
    assert result == [False]
    assert phone._status is PhoneStatus.FAILED


def test_legacy_receive_preserves_large_udp_message_body():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=SimpleNamespace(),
        myIP="127.0.0.1",
    )
    body = b"x" * 12000
    packet = (
        b"SIP/2.0 200 OK\r\nContent-Type: text/plain\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )
    try:
        receiver.bind(("127.0.0.1", 0))
        client.s = receiver
        sender.sendto(packet, receiver.getsockname())

        message = client._recv_message_before(time.monotonic() + 1)

        assert message is not None
        assert message.body_raw == body
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize("offsets", [["0"], ["0", "2d"], ["0", "2d", "4d"]])
def test_sdp_repeat_accepts_one_or_more_offsets_without_discarding_them(
    offsets,
):
    body = ("v=0\r\nt=0 0\r\nr=7d 1h " + " ".join(offsets) + "\r\n").encode()
    message = SIPMessage(
        b"SIP/2.0 200 OK\r\nContent-Type: application/sdp\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body
    )

    assert message.body["r"] == dict(
        repeat="7d",
        duration="1h",
        **{f"offset{index}": value for index, value in enumerate(offsets, 1)},
    )


def test_sdp_repeat_still_requires_an_offset():
    body = b"v=0\r\nr=7d 1h\r\n"
    with pytest.raises(SIPParseError, match="repeat"):
        SIPMessage(
            b"SIP/2.0 200 OK\r\nContent-Type: application/sdp\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
