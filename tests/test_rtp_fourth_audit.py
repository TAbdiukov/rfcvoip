import struct
from types import SimpleNamespace

from rfcvoip import RTP


def test_packet_manager_rebuild_preserves_short_overwrites_and_unwritten_tails():
    manager = RTP.RTPPacketManager()
    manager.write(100, b"abcdef")
    manager.write(102, b"XY")
    manager.write(100, b"12")
    manager.write(98, b"--")

    assert manager.read(8) == b"--12XYef"


def test_packet_manager_rebuild_preserves_newest_overlapping_packet():
    manager = RTP.RTPPacketManager()
    manager.write(100, b"abcdef")
    manager.write(102, b"XY")
    manager.write(101, b"1234")
    manager.write(98, b"--")

    assert manager.read(8) == b"--a1234f"


def test_packet_manager_rebuild_keeps_unread_tail_after_compaction():
    manager = RTP.RTPPacketManager()
    manager.max_log_span = 12
    manager.write(100, b"abcdefghijkl")
    assert manager.read(8) == b"abcdefgh"

    manager.rebuild(False)

    assert manager.read(4) == b"ijkl"


def test_log_pruning_keeps_visible_tail_that_crosses_the_cutoff():
    manager = RTP.RTPPacketManager()
    manager.max_log_span = 20
    manager.write(100, b"abcdefghijklmnopqrst")
    assert manager.read(10) == b"abcdefghij"
    manager.write(125, b"Z")

    manager.rebuild(False)

    assert manager.read(16) == b"klmnopqrst" + b"\x80" * 5 + b"Z"


def test_duplicate_from_previous_ssrc_does_not_replace_the_new_source_audio():
    def client():
        return RTP.RTPClient(
            {0: RTP.PayloadType.PCMU},
            "127.0.0.1",
            0,
            "127.0.0.1",
            40002,
            RTP.TransmitType.SENDRECV,
        )

    original = struct.pack("!BBHII", 0x80, 0, 1, 100, 1) + b"\x80" * 4
    replacement = struct.pack("!BBHII", 0x80, 0, 1, 500, 2) + b"\x10" * 4
    actual, expected = client(), client()
    actual.parse_packet(original)
    actual.parse_packet(replacement)
    expected.parse_packet(replacement)

    actual.parse_packet(original)

    assert actual.read(4, blocking=False) == expected.read(4, blocking=False)
    assert actual._receive_ssrc == 2


def test_receive_socket_errors_do_not_spin_without_backoff(monkeypatch):
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    attempts = []
    waits = []

    def recv(_length):
        attempts.append(None)
        if len(attempts) == 3:
            client.NSD = False
        raise OSError("temporary network failure")

    monkeypatch.setattr(RTP.time, "sleep", waits.append)
    client.sin = SimpleNamespace(recv=recv)
    client.NSD = True
    client.recv()

    assert waits == [0.01, 0.01]
