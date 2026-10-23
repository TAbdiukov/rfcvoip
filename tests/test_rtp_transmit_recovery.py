"""Regressions through the actual PCM-to-RTP boundary, without real sleeps."""
from collections import deque
import math
import struct
from types import SimpleNamespace

import pytest

import rfcvoip
from rfcvoip import RTP


@pytest.fixture
def client():
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1", 40000, "127.0.0.1", 40002,
        RTP.TransmitType.SENDRECV,
        audio_sample_rate=16000, audio_bit_depth=16, audio_channels=1,
    )


def test_output_fifo_recovers_after_empty_reads_and_keeps_prompt_tail(client):
    first, second = b"\1\0" * 320, b"\2\0" * 320
    client.write(first)
    assert client.pmout.read(640) == first
    assert client.pmout.read(640) == bytes(640)
    client.write(second)
    assert client.pmout.read(640) == second
    client.write(b"\3\0" * 10)
    assert client.pmout.read(640) == b"\3\0" * 10 + bytes(620)


def test_default_fifo_preserves_whole_queued_announcement(client):
    now = [0.0]
    client.pmout._clock = lambda: now[0]
    frames = [struct.pack("<h", i) * 320 for i in range(1000)]
    client.write(b"".join(frames))
    assert client.pmout.available() == 640000
    now[0] = 60.0  # Queued announcements do not expire in default mode.
    assert [client.pmout.read(640) for _ in frames] == frames
    assert client.pmout.available() == 0
    assert client.pmout.dropped_bytes == client.pmout.expired_bytes == 0


@pytest.mark.parametrize("silence", [b"\0", b"\x80"])
def test_live_partial_frames_survive_underrun_and_reprime(silence):
    now = [0.0]
    output = RTP.RTPAudioOutput(8, silence, clock=lambda: now[0])
    output.enable_realtime()
    output.write(100, b"abcdefghABCDEFGH")
    assert output.read(8) == b"abcdefgh"
    assert output.read(8) == b"ABCDEFGH"
    output.write(116, b"1234")
    assert output.read(8) == silence * 8
    output.write(120, b"5678")
    assert output.read(8) == silence * 8
    now[0] = 0.041
    assert output.read(8) == b"12345678"
    assert output.underruns == 1


def test_live_latency_is_bounded_and_fresh_audio_recovers(client):
    now = [0.0]
    output = client.pmout
    output._clock = lambda: now[0]
    output.enable_realtime()
    client.write(b"x" * 640 * 20)
    assert output.available() == 640 * 7
    assert output.dropped_bytes == 640 * 13
    now[0] = 0.141
    assert output.read(640) == bytes(640)
    assert output.expired_bytes == 640 * 7
    client.write(b"y" * 640 * 2)
    assert output.read(640) == b"y" * 640


def test_live_overflow_preserves_pcm_sample_channel_alignment():
    output = RTP.RTPAudioOutput(16, b"\0", sample_alignment=4)
    output.enable_realtime(max_latency=0.04, prefill=0)
    output.write(0, b"abcd" * 10 + b"x")
    # The 9-byte excess rounds to a complete stereo PCM16 sample (12 bytes).
    assert output.dropped_bytes == 12
    assert output.read(16) == b"abcd" * 4


@pytest.mark.parametrize("complete_before_expiry", [False, True])
def test_live_expiry_preserves_sample_alignment_across_fragmented_writes(complete_before_expiry):
    now = [0.0]
    output = RTP.RTPAudioOutput(4, b"\0", sample_alignment=4, clock=lambda: now[0])
    output.enable_realtime(prefill=0)
    output.write(0, b"abc")
    now[0] = 0.100
    if complete_before_expiry:
        output.write(3, b"dWXYZ")
    now[0] = 0.150
    if complete_before_expiry:
        assert output.read(4) == b"WXYZ"
    else:
        assert output.read(4) == bytes(4)
        output.write(3, b"dWXYZ")
        assert output.read(4) == b"WXYZ"
    assert output.expired_bytes == 4


def test_coarse_windows_wakeups_do_not_lose_or_duplicate_pcm(client):
    now = [0.0]
    client.pmout._clock = lambda: now[0]
    client.pmout.enable_realtime()
    frames = [struct.pack("<h", 1000 + (i // 2) * 20) * 320 for i in range(1000)]
    adapter = client._codec_adapter(client.preference).__class__()
    adapter.configure_source_format(sample_rate=16000, bit_depth=16, channels=1)
    expected = [adapter.encode(frame) for frame in frames]
    silence = adapter.encode(bytes(640))
    packets, times, traces = [], [], []
    produced = [0]

    def wait(delay):
        deadline = math.ceil((now[0] + delay) / 0.015625 - 1e-9) * 0.015625
        while produced[0] < len(frames) and produced[0] * 0.020 + 0.005 <= deadline + 1e-9:
            now[0] = produced[0] * 0.020 + 0.005
            client.write(frames[produced[0]])
            produced[0] += 1
        now[0] = deadline
        client.NSD = now[0] <= 20.15

    def sendto(packet, _address):
        packets.append(packet)
        times.append(now[0])

    client.sout = SimpleNamespace(sendto=sendto)
    client._callbot_audio_trace = lambda _event, **fields: traces.append(fields)
    client.outSequence, client.outTimestamp = 65530, 0xFFFFFF00
    client.NSD = True
    client.trans(clock=lambda: now[0], wait=wait)
    assert [p[12:] for p in packets if p[12:] != silence] == expected
    assert all(0.004 < b - a < 0.036 for a, b in zip(times, times[1:]))
    assert max(trace["queued_ms"] for trace in traces) <= 40
    assert client.pmout.dropped_bytes == client.pmout.expired_bytes == 0
    assert abs(times[999] - 999 * 0.020) < 0.016
    assert [int.from_bytes(p[2:4], "big") for p in packets] == [
        (65530 + i) & 0xFFFF for i in range(len(packets))
    ]
    assert [int.from_bytes(p[4:8], "big") for p in packets] == [
        (0xFFFFFF00 + i * 160) & 0xFFFFFFFF for i in range(len(packets))
    ]


@pytest.mark.parametrize("duration_ms", [10, 20, 40, 60])
def test_codec_duration_and_opus_rtp_clock_drive_pacing(client, duration_ms):
    from rfcvoip.codecs.opus import OpusCodec

    adapter = object.__new__(OpusCodec)
    adapter.configure_source_format(sample_rate=16000, bit_depth=16, channels=1)
    adapter.frame_duration_ms = duration_ms
    adapter.encode = lambda data: data
    client._codec_adapter = lambda *args, **kwargs: adapter
    now, packets = [0.0], []
    client._send_rtp_packet = lambda _pt, _data, **kw: packets.append((now[0], kw["timestamp"]))
    client.outTimestamp = 1000
    client.NSD = True

    def wait(delay):
        now[0] += delay
        client.NSD = len(packets) < 5

    client.trans(clock=lambda: now[0], wait=wait)
    assert [p[0] for p in packets] == pytest.approx([i * duration_ms / 1000 for i in range(5)])
    assert [p[1] for p in packets] == [1000 + i * 48 * duration_ms for i in range(5)]


def test_long_stall_expires_live_pcm_without_catchup_burst(client):
    now, packets, times = [0.0], [], []
    client.pmout._clock = lambda: now[0]
    client.pmout.enable_realtime()
    client.write(b"\1\0" * 320 * 4)
    client.NSD = True

    def sendto(packet, _address):
        packets.append(packet)
        times.append(now[0])

    def wait(delay):
        now[0] += 0.3 if len(packets) == 2 else delay
        client.NSD = len(packets) < 6

    client.sout = SimpleNamespace(sendto=sendto)
    client.trans(clock=lambda: now[0], wait=wait)
    assert min(b - a for a, b in zip(times, times[1:])) >= 0.019
    timestamps = [int.from_bytes(p[4:8], "big") for p in packets]
    assert timestamps[2] - timestamps[1] == pytest.approx((times[2] - times[1]) * 8000, abs=1)
    assert client.pmout.expired_bytes > 0
    assert client.pmout.available() == 0


def test_slow_encoder_skips_deadlines_and_recovers_without_burst(client):
    now, sent = [0.0], []
    adapter = client._codec_adapter(client.preference)
    encode = adapter.encode
    attempts = [0]
    client.outTimestamp = 1000
    client.NSD = True

    def encode_with_stall(data):
        attempts[0] += 1
        if attempts[0] == 2:
            now[0] += 0.110
        return encode(data)

    def sendto(packet, _address):
        sent.append((now[0], int.from_bytes(packet[4:8], "big")))

    def wait(delay):
        now[0] += delay
        client.NSD = len(sent) < 6

    adapter.encode = encode_with_stall
    client.sout = SimpleNamespace(sendto=sendto)
    client.trans(clock=lambda: now[0], wait=wait)
    assert min(b[0] - a[0] for a, b in zip(sent, sent[1:])) >= 0.019
    assert sent[2][1] - sent[1][1] > 160
    assert [p[1] for p in sent[2:]] == [2120 + i * 160 for i in range(4)]


def test_encode_and_socket_failures_preserve_media_clock_and_recover(client):
    now, packets, attempts = [0.0], [], [0]
    adapter = client._codec_adapter(client.preference)
    encode = adapter.encode
    start = client.outTimestamp
    client.NSD = True

    def encode_once_failed(data):
        attempts[0] += 1
        if attempts[0] == 1:
            raise ValueError("temporary encoder failure")
        return encode(data)

    def sendto(packet, _address):
        if attempts[0] == 2:
            raise BlockingIOError("temporarily full socket")
        packets.append(packet)

    def wait(delay):
        now[0] += delay
        client.NSD = not packets

    adapter.encode = encode_once_failed
    client.sout = SimpleNamespace(sendto=sendto)
    with pytest.warns(RuntimeWarning) as warnings:
        client.trans(clock=lambda: now[0], wait=wait)
    assert len(warnings) == 2
    assert int.from_bytes(packets[0][4:8], "big") == start + 320


def test_legacy_delay_reduction_does_not_accelerate_audio(client, monkeypatch):
    monkeypatch.setattr(rfcvoip, "TRANSMIT_DELAY_REDUCTION", 0.75)
    now, sent = [0.0], []
    client.NSD = True
    client.sout = SimpleNamespace(sendto=lambda _packet, _address: sent.append(now[0]))

    def wait(delay):
        now[0] += delay
        client.NSD = len(sent) < 10

    client.trans(clock=lambda: now[0], wait=wait)
    assert sent == pytest.approx([i * 0.020 for i in range(10)])


def test_receive_only_dtmf_and_diagnostic_failure_do_not_break_transmit(client):
    now, sent, dtmf = [0.0], [], []
    client.NSD = True
    client.sendrecv = RTP.TransmitType.RECVONLY
    client._pending_dtmf = deque("5")
    client.transmit_dtmf = lambda digit: dtmf.append(digit)
    client.sout = SimpleNamespace(sendto=lambda p, _address: sent.append(p))

    def broken_trace(*args, **kwargs):
        raise RuntimeError("diagnostic failure")

    def wait(delay):
        now[0] += delay
        if now[0] >= 0.04:
            client.sendrecv = RTP.TransmitType.SENDRECV
        client.NSD = len(sent) < 3

    client._callbot_audio_trace = broken_trace
    client.trans(clock=lambda: now[0], wait=wait)
    assert dtmf == ["5"]
    assert len(sent) == 3
