import threading
import struct

import pytest

from rfcvoip import RTP


def test_initial_output_frame_size_matches_complete_public_audio_samples():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        audio_sample_rate=11025,
        audio_bit_depth=16,
        audio_channels=2,
        audio_output_mode="realtime",
    )

    assert client.pmout.frame_bytes == client.audio_frame_size() == 880
    assert client.pmout._prefill == 880 * 2
    assert client.pmout._maximum == 880 * 7


@pytest.mark.parametrize("alignment", [2, 4, 6, 16])
def test_buffered_output_keeps_partial_interleaved_sample_until_completed(
    alignment,
):
    size = alignment * 4
    output = RTP.RTPAudioOutput(size, b"\x00", sample_alignment=alignment)
    sample = bytes(range(1, alignment + 1))
    output.write(0, sample + sample[:-1])

    assert output.read(size) == sample + bytes(size - alignment)
    assert output.available() == alignment - 1
    output.write(alignment * 2 - 1, sample[-1:] + sample * 3)

    assert output.read(size) == sample * 4
    assert output.available() == 0


def make_client():
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )


def test_restart_cannot_revive_a_previous_worker_that_is_still_exiting(
    monkeypatch,
):
    client = make_client()
    entered = threading.Event()
    release = threading.Event()

    class FastJoinThread(threading.Thread):
        def join(self, timeout=None):
            return super().join(timeout=0.01)

    def stalled_receiver():
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(RTP, "Thread", FastJoinThread)
    monkeypatch.setattr(client, "recv", stalled_receiver)
    monkeypatch.setattr(client, "trans", lambda: None)
    try:
        client.start()
        assert entered.wait(timeout=1)
        client.stop()

        with pytest.raises(RuntimeError, match="workers are still stopping"):
            client.start()
        assert not client.NSD

        release.set()
        for worker in client._workers:
            threading.Thread.join(worker, timeout=1)
        client.start()
    finally:
        release.set()
        client.stop()


def test_stop_can_run_in_receiver_callback_without_joining_itself(monkeypatch):
    client = make_client()
    stopped = threading.Event()

    def receiver_callback():
        client.stop()
        stopped.set()

    monkeypatch.setattr(client, "recv", receiver_callback)
    monkeypatch.setattr(client, "trans", lambda: None)
    try:
        client.start()
        assert stopped.wait(timeout=1)
        assert not client.NSD
    finally:
        client.stop()


def test_failed_worker_start_releases_socket_and_running_state(monkeypatch):
    client = make_client()
    sockets = []

    class Socket:
        closed = False

        def __init__(self, *_args):
            sockets.append(self)

        def bind(self, _address):
            pass

        def setblocking(self, _blocking):
            pass

        def close(self):
            self.closed = True

    class FailedThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread allocation failed")

        def is_alive(self):
            return False

    monkeypatch.setattr(RTP.socket, "socket", Socket)
    monkeypatch.setattr(RTP, "Thread", FailedThread)

    with pytest.raises(RuntimeError, match="thread allocation failed"):
        client.start()

    assert not client.NSD
    assert client.sin is client.sout is None
    assert sockets[0].closed
    client.stop()


def test_duplicate_telephone_event_start_does_not_repeat_dtmf_callback():
    received = []
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        dtmf=received.append,
    )

    def event(sequence, timestamp, ssrc=1):
        return (
            struct.pack("!BBHII", 0x80, 0x80 | 101, sequence, timestamp, ssrc)
            + b"\x05\x0a\x01\x90"
        )

    first = event(1, 100)
    client.parse_packet(first)
    client.parse_packet(first)
    client.parse_packet(event(2, 100))
    client.parse_packet(event(3, 200))
    client.parse_packet(event(1, 200, ssrc=2))

    assert received == ["5", "5", "5"]


def test_telephone_event_duplicate_history_is_bounded():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        dtmf=lambda _digit: None,
    )
    for index in range(300):
        packet = struct.pack("!BBHII", 0x80, 0x80 | 101, index, index * 800, 1)
        client.parse_packet(packet + b"\x05\x0a\x01\x90")

    assert (
        len(client._received_dtmf) == len(client._received_dtmf_order) == 256
    )


def test_reused_rtp_message_clears_previous_extension_metadata():
    header = struct.pack("!BBHII", 0x90, 0, 1, 100, 1)
    extension = struct.pack("!HH", 0xBEDE, 1) + b"extn"
    message = RTP.RTPMessage(header + extension + b"audio", {})
    assert message.extension_profile == 0xBEDE

    message.parse(struct.pack("!BBHII", 0x80, 0, 2, 260, 1) + b"next")

    assert message.extension is False
    assert message.extension_profile is None
    assert message.extension_payload == b""
    assert message.payload == b"next"
