import pytest

from rfcvoip import RTP
from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs import codec_availability, create_codec


@pytest.mark.parametrize(
    (
        "payload_type",
        "payload_number",
        "rtpmap",
        "fmtp",
        "preferred_source_sample_rate",
        "preferred_public_bit_depth",
    ),
    [
        (
            RTP.PayloadType.PCMU,
            0,
            "0 PCMU/8000",
            [],
            8000,
            8,
        ),
        (
            RTP.PayloadType.PCMA,
            8,
            "8 PCMA/8000",
            [],
            8000,
            8,
        ),
        (
            RTP.PayloadType.PCMU_WB,
            112,
            "112 PCMU-WB/16000",
            ["mode-set=1"],
            16000,
            16,
        ),
        (
            RTP.PayloadType.PCMA_WB,
            113,
            "113 PCMA-WB/16000",
            ["mode-set=1"],
            16000,
            16,
        ),
    ],
)
def test_g711_payloads_are_available_and_advertise_wire_metadata(
    payload_type,
    payload_number,
    rtpmap,
    fmtp,
    preferred_source_sample_rate,
    preferred_public_bit_depth,
):
    availability = codec_availability(payload_type)

    assert availability["available"] is True
    assert availability["can_transmit_audio"] is True
    assert availability["default_payload_type"] == payload_number
    assert availability["preferred_source_sample_rate"] == preferred_source_sample_rate
    assert availability["preferred_public_bit_depth"] == preferred_public_bit_depth

    assert RTP.default_payload_type(payload_type) == payload_number
    assert RTP.rtpmap_for_payload_type(payload_number, payload_type) == rtpmap
    assert RTP.fmtp_for_payload_type(payload_number, payload_type) == fmtp
    assert RTP.is_transmittable_audio_codec(payload_type) is True


@pytest.mark.parametrize(
    "payload_type",
    [
        RTP.PayloadType.PCMU,
        RTP.PayloadType.PCMA,
    ],
)
def test_g711_static_codecs_round_trip_legacy_public_silence(payload_type):
    codec = create_codec(payload_type)
    source = silence_bytes(codec.source_frame_size(), codec.source_bit_depth)

    encoded = codec.encode(source)
    decoded = codec.decode(encoded)

    assert len(source) == 160
    assert len(encoded) == 160
    assert decoded == source


@pytest.mark.parametrize(
    "payload_type",
    [
        RTP.PayloadType.PCMU_WB,
        RTP.PayloadType.PCMA_WB,
    ],
)
def test_g711_wideband_codecs_round_trip_core_mode_silence(payload_type):
    codec = create_codec(payload_type)
    source = silence_bytes(codec.source_frame_size(), codec.source_bit_depth)

    encoded = codec.encode(source)
    decoded = codec.decode(encoded)

    assert len(source) == 320
    assert encoded[0] == 1
    assert len(encoded) == 161
    assert decoded == source


@pytest.mark.parametrize(
    "payload_type",
    [
        RTP.PayloadType.PCMU_WB,
        RTP.PayloadType.PCMA_WB,
    ],
)
def test_g711_wideband_codecs_ignore_received_enhancement_layers(payload_type):
    codec = create_codec(payload_type)
    source = silence_bytes(codec.source_frame_size(), codec.source_bit_depth)
    core_mode_payload = codec.encode(source)

    core_frames = [
        core_mode_payload[offset : offset + 40]
        for offset in range(1, len(core_mode_payload), 40)
    ]
    enhanced_mode_payload = bytes([4]) + b"".join(
        core_frame + (b"\x00" * 20) for core_frame in core_frames
    )

    assert len(core_frames) == 4
    assert len(enhanced_mode_payload) == 241
    assert codec.decode(enhanced_mode_payload) == source


@pytest.mark.parametrize(
    "payload_type",
    [
        RTP.PayloadType.PCMU_WB,
        RTP.PayloadType.PCMA_WB,
    ],
)
def test_g711_wideband_fmtp_requires_core_mode_support(payload_type):
    assert RTP.codec_fmtp_supported(payload_type, []) is True
    assert RTP.codec_fmtp_supported(payload_type, ["mode-set=1"]) is True
    assert RTP.codec_fmtp_supported(payload_type, ["mode-set=1,2"]) is True
    assert RTP.codec_fmtp_supported(payload_type, ["mode-set=2,3,4"]) is False