import rfcvoip
from rfcvoip import RTP
from rfcvoip.codecs import create_codec


def test_pcmu_wb_encodes_g7111_r1_core_payload():
    codec = create_codec(RTP.PayloadType.PCMU_WB)

    payload = codec.encode(b"\x80" * codec.source_frame_size(20))

    assert payload[0] & 0x07 == 1
    assert len(payload) == 161
    assert len(codec.decode(payload)) == codec.source_frame_size(20)


def test_pcma_wb_extracts_l0_core_from_r3_payload():
    pcma = create_codec(RTP.PayloadType.PCMA)
    pcma_wb = create_codec(RTP.PayloadType.PCMA_WB)
    core = pcma.encode(b"\x80" * pcma.source_frame_size(5))

    payload = bytes([4]) + core + (b"\x00" * 20)

    decoded = pcma_wb.decode(payload)
    assert len(decoded) == pcma_wb.source_frame_size(5)


def test_wideband_mode_set_must_allow_r1_for_transmit():
    assert RTP.codec_fmtp_supported(
        RTP.PayloadType.PCMU_WB,
        ["mode-set=4,3,1"],
    )
    assert not RTP.codec_fmtp_supported(
        RTP.PayloadType.PCMU_WB,
        ["mode-set=4,3"],
    )


def test_codec_priority_controls_negotiated_selection():
    rfcvoip.reset_codec_priorities()

    assoc = {
        0: RTP.PayloadType.PCMU,
        112: RTP.PayloadType.PCMU_WB,
    }
    payload_type, codec = RTP.select_transmittable_audio_codec(assoc)
    assert payload_type == 112
    assert codec == RTP.PayloadType.PCMU_WB

    rfcvoip.set_codec_priority(RTP.PayloadType.PCMU, 1200)
    payload_type, codec = RTP.select_transmittable_audio_codec(assoc)
    assert payload_type == 0
    assert codec == RTP.PayloadType.PCMU

    rfcvoip.reset_codec_priorities()