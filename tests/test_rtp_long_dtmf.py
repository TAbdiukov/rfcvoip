import struct
from types import SimpleNamespace

import pytest

from rfcvoip import RTP


def make_client(callback=None):
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        dtmf=callback,
    )


@pytest.mark.parametrize(
    "duration_ms", [100, 8191.875, 8192, 10000, 16383.75, 20000]
)
def test_long_dtmf_splits_at_duration_limit_and_roundtrips_once(
    monkeypatch, duration_ms
):
    sender = make_client()
    received = []
    receiver = make_client(received.append)
    packets = []
    sender.sout = SimpleNamespace(
        sendto=lambda packet, _target: packets.append(packet)
    )
    sender.outTimestamp = start = 0xFFFFFF00
    sender.NSD = True
    monkeypatch.setattr(RTP.time, "sleep", lambda _seconds: None)

    assert sender.transmit_dtmf("5", duration_ms=duration_ms)

    segments = {}
    for packet in packets:
        message = RTP.RTPMessage(packet, sender.assoc)
        segments.setdefault(message.timestamp, []).append(message)
        receiver.parse_packet(packet)
    total_ticks = int(duration_ms * 8)
    expected_segments = (total_ticks + 65534) // 65535
    assert len(segments) == expected_segments
    assert [packet[1] & 0x80 != 0 for packet in packets].count(True) == 1
    for index, (timestamp, reports) in enumerate(segments.items()):
        assert timestamp == (start + index * 65535) & 0xFFFFFFFF
        final = index == expected_segments - 1
        duration = min(65535, total_ticks - index * 65535)
        assert [
            struct.unpack("!H", report.payload[2:])[0] for report in reports
        ][-3:] == [duration] * 3
        assert all(
            bool(report.payload[1] & 0x80) == final for report in reports[-3:]
        )
        assert all(not report.payload[1] & 0x80 for report in reports[:-3])
    assert received == ["5"]
    assert sender.outTimestamp == (start + total_ticks) & 0xFFFFFFFF
