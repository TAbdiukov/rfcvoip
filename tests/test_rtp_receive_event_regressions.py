import struct
import math
from types import SimpleNamespace

import pytest

from rfcvoip import RTP
import rfcvoip.codecs


def make_client(callback=None, mode=RTP.TransmitType.SENDRECV):
    return RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        mode,
        dtmf=callback,
    )


def event_packet(
    sequence, timestamp, *, marker=False, end=False, duration=400, code=5
):
    return struct.pack(
        "!BBHII",
        0x80,
        101 | (0x80 if marker else 0),
        sequence,
        timestamp & 0xFFFFFFFF,
        1,
    ) + struct.pack("!BBH", code, 0x0A | (0x80 if end else 0), duration)


@pytest.mark.parametrize("end", [False, True])
def test_dtmf_is_recovered_when_the_marked_start_packet_is_lost(end):
    received = []
    client = make_client(received.append)
    client.parse_packet(event_packet(2, 100, end=end, duration=800))
    assert received == ["5"]
    client.parse_packet(event_packet(3, 100, end=True, duration=800))
    client.parse_packet(event_packet(1, 100, marker=True))

    assert received == ["5"]


def test_long_dtmf_continuation_segment_is_not_reported_as_a_second_digit():
    received = []
    client = make_client(received.append)
    client.parse_packet(event_packet(1, 100, marker=True))
    client.parse_packet(event_packet(2, 100, duration=65535))
    client.parse_packet(event_packet(3, 100 + 65535, duration=400))
    client.parse_packet(event_packet(4, 100 + 65535, end=True, duration=800))

    assert received == ["5"]


def test_dtmf_callback_failure_does_not_prevent_later_events():
    received = []

    def callback(digit):
        received.append(digit)
        if len(received) == 1:
            raise RuntimeError("application callback failed")

    client = make_client(callback)
    with pytest.warns(RuntimeWarning, match="DTMF callback failed"):
        client.parse_packet(event_packet(1, 100, marker=True))
    client.parse_packet(event_packet(2, 900, marker=True, code=6))

    assert received == ["5", "6"]


@pytest.mark.parametrize("mode", list(RTP.TransmitType))
def test_receiver_only_dispatches_packets_when_reception_is_enabled(mode):
    client = make_client(mode=mode)
    received = []

    def recv(_length):
        client.NSD = False
        return b"datagram"

    client.sin = SimpleNamespace(recv=recv)
    client.parse_packet = received.append
    client.NSD = True
    client.recv()

    assert received == (
        [b"datagram"]
        if mode in (RTP.TransmitType.SENDRECV, RTP.TransmitType.RECVONLY)
        else []
    )


@pytest.mark.parametrize("payload_type", [-1, 128, 500])
def test_invalid_audio_payload_numbers_are_rejected_during_negotiation(
    payload_type,
):
    with pytest.raises(
        RTP.RTPParseError, match="No transmittable audio codec"
    ):
        RTP.select_transmittable_audio_codec(
            {payload_type: RTP.PayloadType.PCMU}
        )

    assert RTP.select_transmittable_audio_codec(
        {payload_type: RTP.PayloadType.PCMU, 8: RTP.PayloadType.PCMA},
        priority_scores={RTP.PayloadType.PCMU: 1000, RTP.PayloadType.PCMA: 1},
    ) == (8, RTP.PayloadType.PCMA)


@pytest.mark.parametrize("payload_type", [-1, 128, 500])
def test_invalid_telephone_event_payload_numbers_cannot_be_queued(
    payload_type,
):
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, payload_type: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )

    assert client.send_dtmf("5") is False


@pytest.mark.parametrize("play_before_late_packet", [False, True])
def test_late_audio_cannot_corrupt_decoder_state_of_following_packets(
    monkeypatch,
    play_before_late_packet,
):
    class StatefulCodec:
        previous = b"x"

        def decode(self, payload):
            pcm = self.previous + payload * 3
            self.previous = payload
            return pcm

        def output_offset(self, timestamp):
            return timestamp

    monkeypatch.setattr(
        rfcvoip.codecs,
        "create_codec",
        lambda *_args, **_kwargs: StatefulCodec(),
    )
    client = make_client()

    def packet(sequence, timestamp, payload):
        return struct.pack("!BBHII", 0x80, 0, sequence, timestamp, 1) + payload

    client.parse_packet(packet(1, 100, b"A"))
    client.parse_packet(packet(3, 108, b"C"))
    if play_before_late_packet:
        client.read(12, blocking=False)
    client.parse_packet(packet(2, 104, b"B"))
    client.parse_packet(packet(4, 112, b"D"))

    assert (
        client.read(4 if play_before_late_packet else 16, blocking=False)[-4:]
        == b"CDDD"
    )


def test_late_pcmu_packet_does_not_change_forward_resampling_state():
    def client():
        return RTP.RTPClient(
            {0: RTP.PayloadType.PCMU},
            "127.0.0.1",
            0,
            "127.0.0.1",
            40002,
            RTP.TransmitType.SENDRECV,
            audio_sample_rate=16000,
            audio_bit_depth=16,
        )

    control, reordered = client(), client()
    packets = [
        struct.pack("!BBHII", 0x80, 0, index, 1000 + index * 160, 1)
        + bytes([value]) * 160
        for index, value in enumerate((0x80, 0xF0, 0x10, 0x20))
    ]
    for index in (0, 2):
        control.parse_packet(packets[index])
        reordered.parse_packet(packets[index])
    reordered.parse_packet(packets[1])
    control.parse_packet(packets[3])
    reordered.parse_packet(packets[3])

    assert (
        reordered.read(2560, blocking=False)[-640:]
        == control.read(
            2560,
            blocking=False,
        )[-640:]
    )


def test_late_opus_packet_does_not_change_forward_decoder_state():
    from rfcvoip.codecs.opus import OpusCodec

    availability = OpusCodec.availability()
    if not availability.available:
        pytest.skip(availability.reason)

    def client():
        return RTP.RTPClient(
            {111: RTP.PayloadType.OPUS},
            "127.0.0.1",
            0,
            "127.0.0.1",
            40002,
            RTP.TransmitType.SENDRECV,
            audio_sample_rate=48000,
            audio_bit_depth=16,
            audio_channels=2,
        )

    control, reordered = client(), client()
    encoder = control._codec_adapter(RTP.PayloadType.OPUS)
    packets = []
    for index in range(4):
        source = b"".join(
            struct.pack("<hh", value, value)
            for sample in range(960)
            for value in [
                round(
                    10000 * math.sin((index * 960 + sample) * (index + 1) / 30)
                )
            ]
        )
        packets.append(
            struct.pack("!BBHII", 0x80, 111, index, index * 960, 1)
            + encoder.encode(source)
        )
    for index in (0, 2):
        control.parse_packet(packets[index])
        reordered.parse_packet(packets[index])
    reordered.parse_packet(packets[1])
    control.parse_packet(packets[3])
    reordered.parse_packet(packets[3])

    assert (
        reordered.read(15360, blocking=False)[-3840:]
        == control.read(
            15360,
            blocking=False,
        )[-3840:]
    )


@pytest.mark.parametrize(
    "mode", [RTP.TransmitType.RECVONLY, RTP.TransmitType.INACTIVE]
)
def test_packet_sender_rechecks_direction_after_a_hold_change(mode):
    client = make_client(mode=mode)
    sent = []
    client.sout = SimpleNamespace(
        sendto=lambda packet, _address: sent.append(packet)
    )
    client.NSD = True

    client._send_rtp_packet(0, b"audio", marker=False, timestamp=100)

    assert sent == []


@pytest.mark.parametrize(
    "running,mode",
    [
        (False, RTP.TransmitType.SENDRECV),
        (True, RTP.TransmitType.RECVONLY),
        (True, RTP.TransmitType.INACTIVE),
    ],
)
def test_immediate_dtmf_reports_failure_when_media_transmission_is_disabled(
    running, mode
):
    client = make_client(mode=mode)
    client.NSD = running

    assert client.transmit_dtmf("5", duration_ms=1, packet_ms=1) is False
