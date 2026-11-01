import struct

import pytest

from rfcvoip import RTP


def client(received):
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        dtmf=received.append,
    )


def report(code, duration, *, end=True):
    return struct.pack("!BBH", code, 10 | (0x80 if end else 0), duration)


def packet(payload, *, timestamp=100, marker=True, sequence=1):
    return (
        struct.pack(
            "!BBHII",
            0x80,
            101 | (0x80 if marker else 0),
            sequence,
            timestamp & 0xFFFFFFFF,
            1,
        )
        + payload
    )


@pytest.mark.parametrize("trailing", [b"x", b"xy", b"xyz"])
def test_partial_telephone_event_report_is_rejected_before_dispatch(trailing):
    received = []
    receiver = client(received)

    with pytest.raises(RTP.RTPParseError, match="telephone-event"):
        receiver.parse_packet(packet(report(5, 400) + trailing))

    assert received == []


def test_packed_events_advance_timestamp_and_deduplicate_individual_repetitions():
    received = []
    receiver = client(received)
    start = 0xFFFFFF00
    packed = packet(report(5, 400) + report(6, 800), timestamp=start)
    receiver.parse_packet(packed)
    assert received == ["5", "6"]
    receiver.parse_packet(packed)
    receiver.parse_packet(
        packet(report(6, 800), timestamp=start + 400, marker=False, sequence=2)
    )

    assert received == ["5", "6"]


def test_invalid_later_packed_report_does_not_dispatch_earlier_digits():
    received = []
    receiver = client(received)

    with pytest.raises(RTP.RTPParseError, match="code 20"):
        receiver.parse_packet(packet(report(5, 400) + report(20, 800)))

    assert received == []


def test_packed_repeated_digit_is_a_distinct_event_after_previous_end():
    received = []
    receiver = client(received)
    receiver.parse_packet(packet(report(5, 400) + report(5, 800)))

    assert received == ["5", "5"]


def test_packed_long_event_continuation_is_not_a_second_digit():
    received = []
    receiver = client(received)
    receiver.parse_packet(packet(report(5, 65535, end=False) + report(5, 400)))

    assert received == ["5"]


def test_packed_marker_for_later_digit_does_not_restart_known_continuation():
    received = []
    receiver = client(received)
    receiver.parse_packet(packet(report(5, 65535, end=False)))
    receiver.parse_packet(
        packet(
            report(5, 400) + report(6, 800), timestamp=100 + 65535, sequence=2
        )
    )

    assert received == ["5", "6"]
