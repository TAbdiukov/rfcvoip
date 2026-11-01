import errno
import socket
import struct
from types import SimpleNamespace

import pytest

from rfcvoip import RTP


def client():
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )


def test_receiver_preserves_large_rtp_udp_datagram(monkeypatch):
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    media = client()
    payload = b"\x80" * 12000
    packet = struct.pack("!BBHII", 0x80, 0, 1, 1000, 1) + payload
    received = []
    parse = media.parse_packet

    def receive(data):
        received.append(data)
        parse(data)
        media.NSD = False

    # On Windows an undersized UDP receive raises WSAEMSGSIZE instead of
    # returning truncated bytes. End the loop on either result.
    monkeypatch.setattr(
        RTP.time, "sleep", lambda _delay: setattr(media, "NSD", False)
    )
    monkeypatch.setattr(media, "parse_packet", receive)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        sender.sendto(packet, receiver.getsockname())
        media.sin = receiver
        media.NSD = True

        media.recv()

        assert received == [packet]
        assert media.pmin.available() == len(payload)
    finally:
        receiver.close()
        sender.close()


def test_permanently_closed_receive_socket_stops_and_cleans_up(monkeypatch):
    media = client()
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.close()
    media.sin = media.sout = receiver
    media.NSD = True
    waits = []

    def stop_retry(delay):
        waits.append(delay)
        media.NSD = False

    monkeypatch.setattr(RTP.time, "sleep", stop_retry)

    media.recv()

    assert waits == []
    assert not media.NSD
    assert media.sin is media.sout is None


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(10054, "ICMP reset"),
        OSError(errno.ENETUNREACH, "unreachable"),
    ],
)
def test_transient_receive_errors_allow_following_audio(monkeypatch, error):
    media = client()
    payload = b"\x80" * 160
    packet = struct.pack("!BBHII", 0x80, 0, 1, 1000, 1) + payload
    results = iter([error, packet])
    waits = []

    def receive(_size):
        result = next(results)
        if isinstance(result, OSError):
            raise result
        media.NSD = False
        return result

    media.sin = SimpleNamespace(recv=receive)
    media.NSD = True
    monkeypatch.setattr(RTP.time, "sleep", waits.append)

    media.recv()

    assert waits == [0.01]
    assert media.pmin.available() == len(payload)
