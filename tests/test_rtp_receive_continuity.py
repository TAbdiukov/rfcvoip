import struct

import pytest

from rfcvoip import RTP
import rfcvoip.codecs


def test_packet_manager_keeps_unread_audio_across_many_compactions():
    manager = RTP.RTPPacketManager(silence_byte=b"\x00")
    # 20 ms of 48 kHz stereo s16 PCM: the old fixed-origin reset skipped
    # queued audio roughly once a second at this public audio format.
    frame_size = 3840

    def frame(index):
        return struct.pack("<I", index) * (frame_size // 4)

    for index in range(3):
        manager.write(1000 + index * frame_size, frame(index))
    for index in range(300):
        assert manager.read(frame_size) == frame(index)
        following = index + 3
        manager.write(1000 + following * frame_size, frame(following))
        assert len(manager.buffer.getvalue()) <= manager.max_log_span


def test_packet_manager_does_not_replay_late_audio_after_playout():
    manager = RTP.RTPPacketManager()
    manager.write(1000, b"abcd")
    assert manager.read(4) == b"abcd"

    manager.write(998, b"late!!")
    manager.write(1004, b"efgh")

    assert manager.read(4) == b"efgh"


def test_packet_manager_can_keep_unplayed_tail_of_overlapping_packet():
    manager = RTP.RTPPacketManager()
    manager.write(1000, b"ab")
    assert manager.read(2) == b"ab"

    manager.write(1000, b"abcd")

    assert manager.read(2) == b"cd"


def test_packet_manager_compacts_an_underrun_without_losing_timeline():
    manager = RTP.RTPPacketManager()
    manager.max_log_span = 20
    manager.write(1000, b"ab")
    assert manager.read(12) == b"ab" + b"\x80" * 10

    manager.write(1002, b"late audio")
    manager.write(1012, b"next")

    assert manager.read(4) == b"next"


def test_packet_manager_accepts_first_offset_larger_than_uint32():
    manager = RTP.RTPPacketManager()
    manager.write((1 << 32) + 2, b"ab")

    assert manager.read(2) == b"ab"


class RecordingCodec:
    def __init__(self):
        self.decoded = []

    def decode(self, payload):
        if payload == b"bad":
            raise ValueError("malformed codec payload")
        self.decoded.append(payload)
        return payload

    def output_offset(self, timestamp):
        return timestamp


def make_client(monkeypatch):
    adapters = []

    def create_codec(*_args, **_kwargs):
        adapter = RecordingCodec()
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(rfcvoip.codecs, "create_codec", create_codec)
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU},
        "127.0.0.1", 40000,
        "127.0.0.1", 40002,
        RTP.TransmitType.SENDRECV,
    )
    return client, adapters


def packet(sequence, timestamp, payload, *, ssrc=1):
    return struct.pack("!BBHII", 0x80, 0, sequence, timestamp, ssrc) + payload


def test_receive_unwraps_timestamps_and_handles_reordering_across_wrap(monkeypatch):
    client, _adapters = make_client(monkeypatch)
    client.parse_packet(packet(10, (1 << 32) - 4, b"abcd"))
    client.parse_packet(packet(12, 4, b"ijkl"))
    client.parse_packet(packet(11, 0, b"efgh"))
    client.parse_packet(packet(13, 8, b"mnop"))

    assert client.read(16, blocking=False) == b"abcdefghijklmnop"
    assert client._receive_timestamp == (1 << 32) + 8


def test_receive_suppresses_duplicates_before_stateful_decode(monkeypatch):
    client, adapters = make_client(monkeypatch)
    client.parse_packet(packet(65535, 100, b"abcd"))
    client.parse_packet(packet(65535, 100, b"dupe"))
    client.parse_packet(packet(0, 104, b"efgh"))

    assert client.read(8, blocking=False) == b"abcdefgh"
    assert adapters[0].decoded == [b"abcd", b"efgh"]


def test_receive_source_switch_resets_pcm_origin_and_decoder(monkeypatch):
    client, adapters = make_client(monkeypatch)
    client.parse_packet(packet(1, 100, b"old!", ssrc=1))
    client.parse_packet(packet(1, 500, b"new!", ssrc=2))

    assert client.read(4, blocking=False) == b"new!"
    assert len(adapters) == 2
    assert adapters[0].decoded == [b"old!"]
    assert adapters[1].decoded == [b"new!"]


def test_receive_codec_cache_is_independent_of_transmission(monkeypatch):
    client, _adapters = make_client(monkeypatch)

    assert client._codec_adapter(RTP.PayloadType.PCMU) is not client._codec_adapter(
        RTP.PayloadType.PCMU, receive=True,
    )


def test_malformed_new_source_does_not_clear_playable_audio(monkeypatch):
    client, adapters = make_client(monkeypatch)
    client.parse_packet(packet(1, 100, b"old!", ssrc=1))

    with pytest.raises(RTP.RTPParseError, match="malformed codec payload"):
        client.parse_packet(packet(1, 500, b"bad", ssrc=2))

    assert client._receive_ssrc == 1
    assert client._receive_codec_adapters[RTP.PayloadType.PCMU] is adapters[0]
    assert client.read(4, blocking=False) == b"old!"


def test_pcmu_resampling_continues_identically_across_timestamp_wrap():
    def client():
        return RTP.RTPClient(
            {0: RTP.PayloadType.PCMU},
            "127.0.0.1", 40000,
            "127.0.0.1", 40002,
            RTP.TransmitType.SENDRECV,
            audio_sample_rate=16000,
            audio_bit_depth=16,
        )

    normal = client()
    wrapped = client()
    for index in range(4):
        payload = bytes((sample + index * 19) % 256 for sample in range(160))
        normal.parse_packet(packet(index, 160 + index * 160, payload))
        wrapped.parse_packet(packet(
            index, ((1 << 32) - 160 + index * 160) & 0xFFFFFFFF, payload,
        ))

    # Decode, resampling state, sample alignment and gap padding must agree
    # with the same stream whose timestamps did not wrap.
    assert wrapped.read(4 * 640, blocking=False) == normal.read(
        4 * 640, blocking=False,
    )
