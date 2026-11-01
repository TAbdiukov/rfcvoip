from types import SimpleNamespace

import pytest

from rfcvoip import RTP
from rfcvoip.codecs import codec_class


@pytest.mark.parametrize(
    "codec", [RTP.PayloadType.PCMU, RTP.PayloadType.PCMU_WB]
)
@pytest.mark.parametrize(
    "hold", [RTP.TransmitType.RECVONLY, RTP.TransmitType.INACTIVE]
)
def test_hold_preserves_elapsed_rtp_clock_without_consuming_queued_audio(
    codec, hold
):
    client = RTP.RTPClient(
        {112: codec},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    now = [0.0]
    sent = []
    frame = client.audio_frame_size()
    client.write(b"\x10" * frame + b"\x20" * frame)
    client.outTimestamp = start = 0xFFFFFF00
    client.NSD = True

    def send(packet, _target):
        sent.append((now[0], packet, client.pmout.available()))
        if len(sent) == 2:
            client.NSD = False

    def wait(delay):
        now[0] += delay
        if len(sent) == 1 and now[0] < 0.120 - 1e-9:
            client.sendrecv = hold
        else:
            client.sendrecv = RTP.TransmitType.SENDRECV

    client.sout = SimpleNamespace(sendto=send)

    client.trans(clock=lambda: now[0], wait=wait)

    assert [item[0] for item in sent] == pytest.approx([0.0, 0.120])
    assert [int.from_bytes(item[1][4:8], "big") for item in sent] == [
        start,
        (start + round(0.120 * codec.rate)) & 0xFFFFFFFF,
    ]
    assert [item[2] for item in sent] == [frame, 0]


def test_hold_after_dtmf_uses_resumed_audio_clock_after_event_end(monkeypatch):
    client = RTP.RTPClient(
        {112: RTP.PayloadType.PCMU_WB, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    now = [0.0]
    sent = []
    client.outTimestamp = 1000
    client.send_dtmf("5")
    client.NSD = True

    def sleep(delay):
        now[0] += delay
        if now[0] >= 0.250 - 1e-9:
            client.sendrecv = RTP.TransmitType.RECVONLY

    def wait(delay):
        now[0] += delay
        if now[0] >= 0.350 - 1e-9:
            client.sendrecv = RTP.TransmitType.SENDRECV

    def send(packet, _target):
        if packet[1] & 0x7F == 112:
            sent.append((now[0], int.from_bytes(packet[4:8], "big")))
            client.NSD = False

    client.sout = SimpleNamespace(sendto=send)
    monkeypatch.setattr(RTP.time, "sleep", sleep)

    client.trans(clock=lambda: now[0], wait=wait)

    assert len(sent) == 1
    assert sent[0] == pytest.approx((0.350, 1000 + 1600 + 2400))


@pytest.mark.parametrize(
    "codec",
    [RTP.PayloadType.PCMU, RTP.PayloadType.PCMU_WB, RTP.PayloadType.OPUS],
)
@pytest.mark.parametrize(
    "digits,hold_seconds", [("55", 0.0), ("5", 0.100), ("55", 0.100)]
)
def test_queued_events_preserve_repeat_and_hold_gaps_in_received_pcm(
    monkeypatch, codec, digits, hold_seconds
):
    if not codec_class(codec).availability().available:
        pytest.skip("Optional codec unavailable")

    def create(callback=None):
        return RTP.RTPClient(
            {112: codec, 101: RTP.PayloadType.EVENT},
            "127.0.0.1",
            0,
            "127.0.0.1",
            40002,
            RTP.TransmitType.SENDRECV,
            dtmf=callback,
            audio_bit_depth=16,
        )

    received = []
    sender, receiver = create(), create(received.append)
    now = [0.0]
    hold_until = [None]
    audio_times, event_starts = [], []
    queued = [False]
    sender.outTimestamp = 0xFFFFFF00
    sender.NSD = True
    sender.write(bytes(sender.audio_frame_size() * 2))

    def send(packet, _target):
        receiver.parse_packet(packet)
        if packet[1] & 0x7F == 112:
            audio_times.append(now[0])
            if len(audio_times) == 2:
                sender.NSD = False
        elif packet[1] & 0x80:
            event_starts.append(int.from_bytes(packet[4:8], "big"))

    def sleep(delay):
        now[0] += delay
        if hold_seconds and hold_until[0] is None and now[0] >= 0.270 - 1e-9:
            sender.sendrecv = RTP.TransmitType.RECVONLY
            hold_until[0] = now[0] + hold_seconds

    def wait(delay):
        now[0] += delay
        if not queued[0]:
            sender.send_dtmf_sequence(digits)
            queued[0] = True
        if hold_until[0] is not None and now[0] >= hold_until[0] - 1e-9:
            sender.sendrecv = RTP.TransmitType.SENDRECV

    sender.sout = SimpleNamespace(sendto=send)
    monkeypatch.setattr(RTP.time, "sleep", sleep)

    sender.trans(clock=lambda: now[0], wait=wait)

    resumed_at = 0.020 + len(digits) * 0.250 + hold_seconds
    assert audio_times == pytest.approx([0.0, resumed_at])
    assert received == list(digits)
    if len(digits) > 1:
        assert (event_starts[1] - event_starts[0]) & 0xFFFFFFFF == (
            2000 + round(hold_seconds * 8000)
        )
    assert receiver.pmin.available() == receiver.audio_frame_size(
        (resumed_at + 0.020) * 1000
    )


@pytest.mark.parametrize("blocked_attempt", [1, 2])
@pytest.mark.parametrize(
    "hold", [RTP.TransmitType.RECVONLY, RTP.TransmitType.INACTIVE]
)
def test_queued_digit_survives_hold_change_before_event_starts(
    monkeypatch, blocked_attempt, hold
):
    sender = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    now, attempts, events = [0.0], [], []
    original = sender.transmit_dtmf
    sender.outTimestamp = 1000
    sender.send_dtmf_sequence("5" * blocked_attempt)
    sender.NSD = True

    def attempt(digit):
        attempts.append(digit)
        if len(attempts) == blocked_attempt:
            sender.sendrecv = hold
        return original(digit)

    def send(packet, _target):
        if packet[1] & 0x7F == 101:
            events.append(packet)
        else:
            sender.NSD = False

    def wait(delay):
        now[0] += delay
        sender.sendrecv = RTP.TransmitType.SENDRECV

    sender.sout = SimpleNamespace(sendto=send)
    monkeypatch.setattr(sender, "transmit_dtmf", attempt)
    monkeypatch.setattr(
        RTP.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )

    sender.trans(clock=lambda: now[0], wait=wait)

    assert attempts == ["5"] * (blocked_attempt + 1)
    assert len(events) > 0
    assert events[0][1] & 0x80
    if blocked_attempt == 2:
        starts = [
            int.from_bytes(item[4:8], "big")
            for item in events
            if item[1] & 0x80
        ]
        assert starts == [1000, 1000 + 2160]
