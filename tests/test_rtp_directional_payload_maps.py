import struct

from rfcvoip import RTP
from rfcvoip.codecs import create_codec


def test_distinct_receive_payload_map_preserves_outgoing_selection():
    receiver = RTP.RTPClient(
        {114: RTP.PayloadType.PCMU_WB, 102: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        receive_assoc={
            112: RTP.PayloadType.PCMU_WB,
            101: RTP.PayloadType.EVENT,
        },
    )
    encoder = create_codec(RTP.PayloadType.PCMU_WB)
    payload = encoder.encode(b"\x80" * encoder.source_frame_size())
    packet = struct.pack("!BBHII", 0x80, 112, 1, 0, 1) + payload

    receiver.parse_packet(packet)

    assert receiver.preference_payload_type == 114
    assert receiver._telephone_event_pt == 102
    assert receiver.pmin.available() == receiver.audio_frame_size()


def test_directional_telephone_event_map_dispatches_original_offered_payload():
    received = []
    receiver = RTP.RTPClient(
        {97: RTP.PayloadType.PCMU, 102: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        dtmf=received.append,
        receive_assoc={96: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
    )
    packet = struct.pack("!BBHII", 0x80, 0x80 | 101, 1, 0, 1)

    receiver.parse_packet(packet + struct.pack("!BBH", 5, 0x8A, 400))

    assert received == ["5"]


def test_default_receive_payload_map_matches_transmission():
    receiver = RTP.RTPClient(
        {97: RTP.PayloadType.PCMU},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    packet = struct.pack("!BBHII", 0x80, 97, 1, 0, 1)

    receiver.parse_packet(packet + b"\x80" * 160)

    assert receiver.pmin.available() == 160
