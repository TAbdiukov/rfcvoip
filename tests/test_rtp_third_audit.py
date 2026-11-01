import tracemalloc
import struct
import threading
from types import SimpleNamespace

import pytest

from rfcvoip import RTP
import rfcvoip.codecs


def test_realtime_output_releases_the_discarded_backing_buffer():
    output = RTP.RTPAudioOutput(160)
    output.enable_realtime()
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        payload = b"x" * 4_000_000
        output.write(0, payload)
        del payload
        retained, _ = tracemalloc.get_traced_memory()

        assert output.available() == 160 * 7
        assert retained - before < 100_000
        assert output.read(160) == b"x" * 160
    finally:
        if not was_tracing:
            tracemalloc.stop()


def test_peer_fmtp_is_copied_and_applied_before_first_encode(monkeypatch):
    class ConfiguredCodec:
        def configure_fmtp(self, settings):
            self.stereo = "stereo=1" in settings

        def encode(self, _payload):
            return b"stereo" if self.stereo else b"mono"

    monkeypatch.setattr(
        rfcvoip.codecs,
        "create_codec",
        lambda *_args, **_kwargs: ConfiguredCodec(),
    )
    fmtp = {97: ["stereo=1"]}
    client = RTP.RTPClient(
        {97: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        codec_fmtp=fmtp,
    )
    fmtp[97].clear()

    assert client.encode_packet(b"audio") == b"stereo"


def test_invalid_peer_fmtp_is_reported_as_codec_initialization_failure(
    monkeypatch,
):
    class ConfiguredCodec:
        def configure_fmtp(self, _settings):
            raise ValueError("unsupported peer configuration")

    monkeypatch.setattr(
        rfcvoip.codecs,
        "create_codec",
        lambda *_args, **_kwargs: ConfiguredCodec(),
    )
    client = RTP.RTPClient(
        {97: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        codec_fmtp={97: ["bad=1"]},
    )

    with pytest.raises(
        RTP.UnsupportedCodecError, match="unsupported peer configuration"
    ):
        client.encode_packet(b"audio")


def test_blocking_read_can_collect_more_than_the_receive_buffer_span(
    monkeypatch,
):
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    frames = [bytes([index]) * 4 for index in range(16)]
    client.pmin.max_log_span = 16
    client.pmin.write(100, frames[0])
    next_frame = [1]

    def produce(_delay):
        index = next_frame[0]
        if index == len(frames):
            client.NSD = False
            return
        client.pmin.write(100 + index * 4, frames[index])
        next_frame[0] += 1

    monkeypatch.setattr(RTP.time, "sleep", produce)
    client.NSD = True
    try:
        assert client.read(64, blocking=True) == b"".join(frames)
    finally:
        client.NSD = False


@pytest.mark.parametrize("sequence", [1, 2])
def test_restarted_client_accepts_a_fresh_receive_timeline(
    monkeypatch, sequence
):
    class NoopThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    def client():
        return RTP.RTPClient(
            {0: RTP.PayloadType.PCMU},
            "127.0.0.1",
            0,
            "127.0.0.1",
            40002,
            RTP.TransmitType.SENDRECV,
        )

    monkeypatch.setattr(RTP, "Thread", NoopThread)
    restarted, fresh = client(), client()
    original = struct.pack("!BBHII", 0x80, 0, 1, 1000, 1) + b"\x80" * 160
    incoming = struct.pack("!BBHII", 0x80, 0, sequence, 0, 1) + b"\x10" * 160
    try:
        restarted.start()
        restarted.parse_packet(original)
        restarted.read(160, blocking=False)
        restarted.stop()
        restarted.start()
        restarted.parse_packet(incoming)
        fresh.parse_packet(incoming)

        assert restarted.read(160, blocking=False) == fresh.read(
            160, blocking=False
        )
    finally:
        restarted.stop()


def test_concurrent_rtp_sends_use_distinct_sequence_numbers():
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    first_send_entered = threading.Event()
    second_send_entered = threading.Event()
    sent = []
    sent_lock = threading.Lock()

    def capture(packet, _address):
        with sent_lock:
            sent.append(packet)
            first = len(sent) == 1
        if first:
            first_send_entered.set()
            second_send_entered.wait(timeout=0.1)
        else:
            second_send_entered.set()

    client.sout = SimpleNamespace(sendto=capture)
    client.outSequence = 65535
    client.NSD = True

    def send():
        client._send_rtp_packet(0, b"audio", marker=False, timestamp=100)

    first = threading.Thread(target=send)
    second = threading.Thread(target=send)
    try:
        first.start()
        assert first_send_entered.wait(timeout=1)
        second.start()
        first.join(timeout=1)
        second.join(timeout=1)

        assert not first.is_alive() and not second.is_alive()
        assert [int.from_bytes(packet[2:4], "big") for packet in sent] == [
            65535,
            0,
        ]
        assert client.outSequence == 1
    finally:
        client.NSD = False
