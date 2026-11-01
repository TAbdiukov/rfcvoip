import struct
from types import SimpleNamespace

import pytest

from rfcvoip import RTP
from rfcvoip.codecs import codec_class


@pytest.fixture(params=[RTP.PayloadType.PCMU_WB, RTP.PayloadType.OPUS])
def codec(request):
    if not codec_class(request.param).availability().available:
        pytest.skip("Optional codec unavailable")
    return request.param


def client(codec, callback=None):
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


def event(sequence, timestamp, *, duration=1600, code=5):
    return packet(
        101, sequence, timestamp, struct.pack("!BBH", code, 0x8A, duration)
    )


def test_mixed_clock_sender_continues_from_event_clock_and_repeated_digits(
    codec, monkeypatch
):
    sender = client(codec)
    sent = []
    sender.sout = SimpleNamespace(
        sendto=lambda data, _target: sent.append(data)
    )
    sender.outSequence = 65534
    sender.outTimestamp = start = 0xFFFFFF00
    sender.NSD = True
    monkeypatch.setattr(RTP.time, "sleep", lambda _seconds: None)

    sender.transmit_dtmf("5", duration_ms=200)
    second_start = sender.outTimestamp
    sender.transmit_dtmf("5", duration_ms=200)

    assert second_start == (start + 1600) & 0xFFFFFFFF
    assert sender.outTimestamp == (start + 3200) & 0xFFFFFFFF
    assert [int.from_bytes(data[2:4], "big") for data in sent] == [
        (65534 + index) & 0xFFFF for index in range(len(sent))
    ]
    received = []
    receiver = client(codec, received.append)
    for data in sent:
        receiver.parse_packet(data)
    assert received == ["5", "5"]


@pytest.mark.parametrize("callback", [None, lambda _digit: None])
def test_event_clock_places_following_audio_after_full_duration(
    codec, callback
):
    receiver = client(codec, callback)
    encoder = receiver._codec_adapter(codec)
    frame = bytes(encoder.source_frame_size())
    payload = encoder.encode(frame)
    increment = encoder.rtp_timestamp_increment(frame, payload)
    start = 0xFFFFFF00
    receiver.parse_packet(packet(112, 65534, start, payload))
    receiver.parse_packet(event(65535, start + increment))
    receiver.parse_packet(packet(112, 0, start + increment + 1600, payload))

    expected = 2 * len(frame) + receiver.audio_frame_size(200)
    assert receiver.pmin.available() == expected


def test_reordered_pre_event_audio_uses_its_original_clock_epoch(codec):
    receiver = client(codec)
    encoder = receiver._codec_adapter(codec)
    frame = bytes(encoder.source_frame_size())
    payload = encoder.encode(frame)
    increment = encoder.rtp_timestamp_increment(frame, payload)
    start = 0xFFFFF000
    first = packet(112, 65533, start, payload)
    late = packet(112, 65534, start + increment, payload)
    tone = event(65535, start + increment * 2)
    following = packet(112, 0, start + increment * 2 + 1600, payload)
    for data in [first, tone, following, late]:
        receiver.parse_packet(data)

    receiver.read(len(frame) * 2, blocking=False)
    assert receiver.read(
        receiver.audio_frame_size(200), blocking=False
    ) == bytes(receiver.audio_frame_size(200))
    assert receiver.pmin.available() == len(frame)


def test_invalid_event_cannot_switch_audio_clock_epoch(codec):
    receiver = client(codec)
    encoder = receiver._codec_adapter(codec)
    frame = bytes(encoder.source_frame_size())
    payload = encoder.encode(frame)
    increment = encoder.rtp_timestamp_increment(frame, payload)
    receiver.parse_packet(packet(112, 1, 1000, payload))
    with pytest.raises(RTP.RTPParseError):
        receiver.parse_packet(event(2, 1000 + increment, code=20))
    receiver.parse_packet(packet(112, 3, 1000 + increment, payload))

    assert receiver.pmin.available() == len(frame) * 2


def test_consecutive_same_digit_events_keep_the_full_pcm_gap(codec):
    received = []
    receiver = client(codec, received.append)
    encoder = receiver._codec_adapter(codec)
    frame = bytes(encoder.source_frame_size())
    payload = encoder.encode(frame)
    increment = encoder.rtp_timestamp_increment(frame, payload)
    start = 0xFFFFFF00
    receiver.parse_packet(packet(112, 65534, start, payload))
    receiver.parse_packet(event(65535, start + increment))
    receiver.parse_packet(event(0, start + increment + 1600))
    receiver.parse_packet(packet(112, 1, start + increment + 3200, payload))

    assert received == ["5", "5"]
    assert receiver.pmin.available() == (
        len(frame) * 2 + receiver.audio_frame_size(400)
    )


def test_clock_epochs_switch_sources_only_after_successful_audio_decode():
    codec = RTP.PayloadType.PCMU_WB
    receiver = client(codec)
    encoder = receiver._codec_adapter(codec)
    frame = bytes(encoder.source_frame_size())
    payload = encoder.encode(frame)
    receiver.parse_packet(packet(112, 1, 1000, payload))
    receiver.parse_packet(event(2, 1320))
    epochs = list(receiver._receive_clock_epochs)
    invalid = bytearray(packet(112, 1, 0, b"\x07"))
    invalid[8:12] = (2).to_bytes(4, "big")

    with pytest.raises(RTP.RTPParseError):
        receiver.parse_packet(bytes(invalid))

    assert list(receiver._receive_clock_epochs) == epochs
    replacement = bytearray(packet(112, 1, 0, payload))
    replacement[8:12] = (2).to_bytes(4, "big")
    receiver.parse_packet(bytes(replacement))

    assert receiver._receive_ssrc == 2
    assert receiver.pmin.available() == len(frame)
    assert len(receiver._receive_clock_epochs) == 1
