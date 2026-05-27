import pytest

import rfcvoip.RTP as rtp
from rfcvoip.audio_format import silence_bytes


def _packet(
    payload_type,
    payload=b"",
    *,
    marker=False,
    sequence=0x1234,
    timestamp=0x01020304,
    ssrc=0x0A0B0C0D,
    csrcs=(),
    extension_profile=None,
    extension_payload=b"",
    padding=False,
):
    first_byte = 0x80
    if padding:
        first_byte |= 0x20
    if extension_profile is not None:
        first_byte |= 0x10
    first_byte |= len(csrcs)

    second_byte = payload_type & 0x7F
    if marker:
        second_byte |= 0x80

    data = bytes([first_byte, second_byte])
    data += int(sequence).to_bytes(2, "big")
    data += int(timestamp).to_bytes(4, "big")
    data += int(ssrc).to_bytes(4, "big")
    data += b"".join(csrcs)

    if extension_profile is not None:
        data += int(extension_profile).to_bytes(2, "big")
        data += (len(extension_payload) // 4).to_bytes(2, "big")
        data += extension_payload

    return data + payload


def test_payload_name_resolution_and_media_classification():
    assert (
        rtp.payload_type_from_name("PCMU", rate=8000, channels=1)
        is rtp.PayloadType.PCMU
    )
    assert (
        rtp.payload_type_from_name("SILK", rate=16000)
        is rtp.PayloadType.SILK_16000
    )
    assert (
        rtp.payload_type_from_name("opus", rate=48000, channels=2)
        is rtp.PayloadType.OPUS
    )

    assert rtp.payload_type_media_kind(rtp.PayloadType.EVENT) == "event"
    assert not rtp.is_audio_codec(rtp.PayloadType.EVENT)
    assert rtp.is_audio_codec(rtp.PayloadType.PCMU)
    assert rtp.is_video_codec(rtp.PayloadType.JPEG)
    assert rtp.is_audio_codec(rtp.PayloadType.MP2T)
    assert rtp.is_video_codec(rtp.PayloadType.MP2T)

    with pytest.raises(ValueError, match="SILK.*44100"):
        rtp.payload_type_from_name("SILK", rate=44100)


def test_select_transmittable_codec_respects_priority_and_payload_number():
    assoc = {
        101: rtp.PayloadType.EVENT,
        97: rtp.PayloadType.PCMU,
        8: rtp.PayloadType.PCMA,
    }

    payload_number, codec = rtp.select_transmittable_audio_codec(
        assoc,
        priority_scores={
            rtp.PayloadType.PCMU: 1000,
            rtp.PayloadType.PCMA: 1,
        },
        enabled_codecs=(
            rtp.PayloadType.PCMU,
            rtp.PayloadType.PCMA,
            rtp.PayloadType.EVENT,
        ),
    )

    assert payload_number == 97
    assert codec is rtp.PayloadType.PCMU


def test_select_transmittable_codec_rejects_event_and_video_only_maps():
    assoc = {
        101: rtp.PayloadType.EVENT,
        26: rtp.PayloadType.JPEG,
    }

    with pytest.raises(
        rtp.RTPParseError,
        match="No transmittable audio codec negotiated",
    ):
        rtp.select_transmittable_audio_codec(
            assoc,
            enabled_codecs=(rtp.PayloadType.EVENT, rtp.PayloadType.JPEG),
        )


def test_dtmf_normalisation_and_client_queueing_without_socket():
    assert rtp.normalize_dtmf_sequence(" 1 2 a # ") == "12A#"
    assert rtp.normalize_dtmf_sequence("A", allow_abcd=False) == ""
    assert rtp.normalize_dtmf_digit("9") == "9"
    assert rtp.normalize_dtmf_digit("12") == ""

    client = rtp.RTPClient(
        {0: rtp.PayloadType.PCMU, 101: rtp.PayloadType.EVENT},
        "127.0.0.1",
        40000,
        "127.0.0.1",
        40002,
        rtp.TransmitType.SENDRECV,
        enabled_codecs=(rtp.PayloadType.PCMU, rtp.PayloadType.EVENT),
    )

    assert client.send_dtmf_sequence(" 1 2 # ") is True
    with client._dtmf_lock:
        assert "".join(client._pending_dtmf) == "12#"

    assert client.send_dtmf_sequence("12x") is False

    no_event_client = rtp.RTPClient(
        {0: rtp.PayloadType.PCMU},
        "127.0.0.1",
        40004,
        "127.0.0.1",
        40006,
        rtp.TransmitType.SENDRECV,
        enabled_codecs=(rtp.PayloadType.PCMU,),
    )
    assert no_event_client.send_dtmf_sequence("1") is False


def test_transmit_dtmf_builds_telephone_event_packets(monkeypatch):
    client = rtp.RTPClient(
        {0: rtp.PayloadType.PCMU, 101: rtp.PayloadType.EVENT},
        "127.0.0.1",
        40000,
        "127.0.0.1",
        40002,
        rtp.TransmitType.SENDRECV,
        enabled_codecs=(rtp.PayloadType.PCMU, rtp.PayloadType.EVENT),
    )
    sent = []

    def capture_send(payload_type, payload, *, marker, timestamp):
        sent.append(
            {
                "payload_type": payload_type,
                "payload": payload,
                "marker": marker,
                "timestamp": timestamp,
            }
        )

    monkeypatch.setattr(client, "_send_rtp_packet", capture_send)
    monkeypatch.setattr(rtp.time, "sleep", lambda _seconds: None)

    start_timestamp = client.outTimestamp
    client.NSD = True
    try:
        assert client.transmit_dtmf(
            "5",
            duration_ms=100,
            packet_ms=50,
            volume=9,
        )
    finally:
        client.NSD = False

    assert len(sent) == 4
    assert {packet["payload_type"] for packet in sent} == {101}
    assert {packet["timestamp"] for packet in sent} == {start_timestamp}

    assert sent[0]["marker"] is True
    assert sent[0]["payload"][0] == 5
    assert sent[0]["payload"][1] == 9
    assert sent[0]["payload"][2:] == (400).to_bytes(2, "big")

    assert sent[-1]["marker"] is False
    assert sent[-1]["payload"][1] == 0x89
    assert sent[-1]["payload"][2:] == (800).to_bytes(2, "big")
    assert client.outTimestamp == (start_timestamp + 800) & 0xFFFFFFFF


def test_rtp_message_parses_csrc_extension_padding_and_dynamic_payload():
    packet = _packet(
        96,
        b"voice" + b"\x00\x00\x00\x04",
        marker=True,
        sequence=0xBEEF,
        timestamp=0x01020304,
        ssrc=0x0A0B0C0D,
        csrcs=(b"csrc",),
        extension_profile=0xBEDE,
        extension_payload=b"extn",
        padding=True,
    )

    message = rtp.RTPMessage(packet, {96: rtp.PayloadType.OPUS})

    assert message.version == 2
    assert message.padding is True
    assert message.extension is True
    assert message.CC == 1
    assert message.marker is True
    assert message.payload_type is rtp.PayloadType.OPUS
    assert message.sequence == 0xBEEF
    assert message.timestamp == 0x01020304
    assert message.SSRC == 0x0A0B0C0D
    assert message.CSRC == [b"csrc"]
    assert message.extension_profile == 0xBEDE
    assert message.extension_payload == b"extn"
    assert message.payload == b"voice"


def test_rtp_message_rejects_short_packets_and_bad_padding():
    with pytest.raises(rtp.RTPParseError, match="too short"):
        rtp.RTPMessage(b"\x80", {})

    with pytest.raises(rtp.RTPParseError, match="padding flag set"):
        rtp.RTPMessage(_packet(0, b"", padding=True), {})

    with pytest.raises(rtp.RTPParseError, match="invalid padding"):
        rtp.RTPMessage(_packet(0, b"abc\x05", padding=True), {})


def test_packet_manager_rebuilds_out_of_order_audio_and_fills_gaps():
    manager = rtp.RTPPacketManager(silence_byte=b"\x00")

    manager.write(104, b"cc")
    manager.write(100, b"aa")

    assert manager.read(6) == b"aa\x00\x00cc"
    assert manager.read(2) == b"\x00\x00"


def test_rtp_client_uses_negotiated_payload_and_decodes_without_socket():
    client = rtp.RTPClient(
        {97: rtp.PayloadType.PCMU, 101: rtp.PayloadType.EVENT},
        "127.0.0.1",
        40000,
        "127.0.0.1",
        40002,
        rtp.TransmitType.SENDRECV,
        audio_bit_depth="best",
        enabled_codecs=(rtp.PayloadType.PCMU, rtp.PayloadType.EVENT),
    )

    assert client.preference_payload_type == 97
    assert client.preference is rtp.PayloadType.PCMU

    audio_format = client.audio_format()
    assert audio_format["sample_rate"] == 8000
    assert audio_format["channels"] == 1
    assert audio_format["bit_depth"] == 8

    frame_size = client.audio_frame_size()
    assert frame_size == 160

    source_frame = silence_bytes(frame_size, client.audio_bit_depth)
    encoded = client.encode_packet(source_frame)
    assert len(encoded) == frame_size

    client.parse_packet(_packet(97, encoded, timestamp=0))
    decoded = client.read(blocking=False)
    assert len(decoded) == frame_size


def test_rtp_client_rejects_mixed_local_and_remote_ip_versions():
    with pytest.raises(rtp.RTPParseError, match="different IP versions"):
        rtp.RTPClient(
            {0: rtp.PayloadType.PCMU},
            "127.0.0.1",
            40000,
            "::1",
            40002,
            rtp.TransmitType.SENDRECV,
            enabled_codecs=(rtp.PayloadType.PCMU,),
        )