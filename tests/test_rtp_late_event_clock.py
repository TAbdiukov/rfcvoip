import itertools
import struct

import pytest

from rfcvoip import RTP


def client():
    return RTP.RTPClient(
        {112: RTP.PayloadType.PCMU_WB, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        audio_bit_depth=16,
    )


def packet(payload_type, sequence, timestamp, payload):
    return (
        struct.pack(
            "!BBHII",
            0x80,
            payload_type,
            sequence & 0xFFFF,
            timestamp & 0xFFFFFFFF,
            1,
        )
        + payload
    )


def media_packets(receiver, *, duration=1600, skip_first_audio=False):
    encoder = receiver._codec_adapter(RTP.PayloadType.PCMU_WB)
    size = encoder.source_frame_size()
    payload = encoder.encode(bytes(size))
    start = 0xFFFFFF00
    return (
        size,
        packet(112, 65533, start, payload),
        packet(101, 65534, start + 320, struct.pack("!BBH", 5, 0x0A, 400)),
        packet(
            101, 65535, start + 320, struct.pack("!BBH", 5, 0x8A, duration)
        ),
        packet(
            112,
            1 if skip_first_audio else 0,
            start + 320 + duration + (320 if skip_first_audio else 0),
            payload,
        ),
        packet(
            112,
            2 if skip_first_audio else 1,
            start + 640 + duration + (320 if skip_first_audio else 0),
            payload,
        ),
    )


@pytest.mark.parametrize("skip_first_audio", [False, True])
@pytest.mark.parametrize(
    "arrival_order", ["late-first-event", "late-final-event"]
)
def test_late_event_repairs_pending_audio_timing(
    arrival_order, skip_first_audio
):
    receiver = client()
    size, first, initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=skip_first_audio
    )
    receiver.parse_packet(first)
    if arrival_order == "late-final-event":
        receiver.parse_packet(initial)
    receiver.parse_packet(resumed)
    receiver.parse_packet(final)
    receiver.parse_packet(following)

    expected = size * (3 + skip_first_audio) + receiver.audio_frame_size(200)
    assert receiver.pmin.available() == expected
    assert receiver.read(size, blocking=False) != b""
    gap = receiver.audio_frame_size(200 + 20 * skip_first_audio)
    assert receiver.read(gap, blocking=False) == bytes(gap)
    assert receiver.pmin.available() == size * 2


def test_late_final_event_does_not_stretch_missing_resumed_audio():
    receiver = client()
    size, first, _initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=True
    )
    for data in [first, final, resumed, following]:
        receiver.parse_packet(data)

    assert receiver.pmin.available() == size * 4 + receiver.audio_frame_size(
        200
    )


@pytest.mark.parametrize("read_frames", [1, 2])
def test_clock_repair_preserves_playout_cursor_and_does_not_repeat_audio(
    read_frames,
):
    receiver = client()
    size, first, _initial, final, resumed, following = media_packets(receiver)
    receiver.parse_packet(first)
    receiver.parse_packet(resumed)
    receiver.read(size * read_frames, blocking=False)
    cursor = receiver.pmin.offset + receiver.pmin.buffer.tell()

    receiver.parse_packet(final)
    receiver.parse_packet(final)
    receiver.parse_packet(following)

    assert receiver.pmin.offset + receiver.pmin.buffer.tell() == cursor
    assert receiver.pmin.available() == (
        size * (3 - read_frames) + receiver.audio_frame_size(200)
    )


def test_clock_repair_bounds_large_new_gaps():
    receiver = client()
    size, first, _initial, final, resumed, _following = media_packets(
        receiver, duration=60000
    )
    receiver.parse_packet(first)
    receiver.parse_packet(resumed)
    receiver.parse_packet(final)

    assert receiver.pmin.available() == size
    assert len(receiver.pmin.buffer.getvalue()) <= receiver.pmin.max_log_span


def test_retiming_replays_only_the_unread_tail_of_a_nonzero_frame():
    receiver = client()
    size, first, _initial, final, _resumed, _following = media_packets(
        receiver
    )
    encoder = receiver._codec_adapter(RTP.PayloadType.PCMU_WB)
    source = b"".join(
        struct.pack("<h", 1000 + index * 10) for index in range(size // 2)
    )
    resumed = packet(112, 0, 0xFFFFFF00 + 1920, encoder.encode(source))
    receiver.parse_packet(first)
    receiver.parse_packet(resumed)
    old_available = receiver.pmin.available()
    receiver.read(old_available - size // 2, blocking=False)
    cursor = receiver.pmin.offset + receiver.pmin.buffer.tell()
    tail = receiver.pmin.buffer.getvalue()[receiver.pmin.buffer.tell() :]
    assert any(tail)

    receiver.parse_packet(final)
    receiver.parse_packet(final)

    assert receiver.pmin.offset + receiver.pmin.buffer.tell() == cursor
    assert receiver.read(3200, blocking=False) == bytes(3200)
    assert receiver.read(len(tail), blocking=False) == tail
    assert receiver.pmin.available() == 0


def test_final_report_sent_after_resumed_audio_still_repairs_end_anchor():
    receiver = client()
    size, first, initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=True
    )
    late = bytearray(final)
    late[2:4] = (3).to_bytes(2, "big")
    for data in [first, initial, resumed, bytes(late), following]:
        receiver.parse_packet(data)

    assert receiver.pmin.available() == size * 4 + receiver.audio_frame_size(
        200
    )


def test_event_older_than_metadata_window_cannot_retime_current_pcm():
    receiver = client()
    size, first, _initial, final, resumed, _following = media_packets(receiver)
    receiver.parse_packet(first)
    receiver.read(size, blocking=False)
    payload = resumed[12:]
    for index in range(300):
        receiver.parse_packet(
            packet(112, index + 4, 0xFFFFFF00 + 1920 + 320 * index, payload)
        )
        receiver.read(size, blocking=False)
    epochs = list(receiver._receive_clock_epochs)
    history = list(receiver._receive_media_history)
    queued = receiver.pmin.buffer.getvalue()
    cursor = receiver.pmin.offset + receiver.pmin.buffer.tell()

    receiver.parse_packet(final)
    receiver.parse_packet(final)

    assert list(receiver._receive_clock_epochs) == epochs
    assert list(receiver._receive_media_history) == history
    assert receiver.pmin.buffer.getvalue() == queued
    assert receiver.pmin.offset + receiver.pmin.buffer.tell() == cursor
    assert len(history) <= 256


@pytest.mark.parametrize("skip_first_audio", [False, True])
@pytest.mark.parametrize(
    "arrival_order", list(itertools.permutations(range(4)))
)
def test_event_and_resumed_audio_arrival_permutations(
    arrival_order, skip_first_audio
):
    receiver = client()
    size, first, initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=skip_first_audio
    )
    receiver.parse_packet(first)
    packets = (initial, final, resumed, following)
    for index in arrival_order:
        receiver.parse_packet(packets[index])

    assert receiver.pmin.available() == (
        size * (3 + skip_first_audio) + receiver.audio_frame_size(200)
    )


@pytest.mark.parametrize("skip_first_audio", [False, True])
def test_final_retransmission_after_resumption_recovers_lost_initial_reports(
    skip_first_audio,
):
    receiver = client()
    size, first, _initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=skip_first_audio
    )
    late = bytearray(final)
    late[2:4] = (3).to_bytes(2, "big")
    next_audio = bytearray(following)
    next_audio[2:4] = (4).to_bytes(2, "big")
    for data in [first, resumed, bytes(late), bytes(next_audio)]:
        receiver.parse_packet(data)

    assert receiver.pmin.available() == (
        size * (3 + skip_first_audio) + receiver.audio_frame_size(200)
    )


@pytest.mark.parametrize("skip_first_audio", [False, True])
@pytest.mark.parametrize(
    "arrival_order",
    list(itertools.permutations(range(4)))
    + list(itertools.permutations(range(1, 4))),
)
def test_later_sequence_final_retransmission_arrival_permutations(
    arrival_order, skip_first_audio
):
    receiver = client()
    size, first, initial, final, resumed, following = media_packets(
        receiver, skip_first_audio=skip_first_audio
    )
    late = bytearray(final)
    late[2:4] = (3).to_bytes(2, "big")
    next_audio = bytearray(following)
    next_audio[2:4] = (4).to_bytes(2, "big")
    receiver.parse_packet(first)
    packets = (initial, bytes(late), resumed, bytes(next_audio))
    for index in arrival_order:
        receiver.parse_packet(packets[index])

    assert receiver.pmin.available() == (
        size * (3 + skip_first_audio) + receiver.audio_frame_size(200)
    )
