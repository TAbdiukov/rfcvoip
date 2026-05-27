import pytest

from rfcvoip.audio_format import (
    AUTO_PUBLIC_AUDIO_BIT_DEPTH,
    PublicAudioFormat,
    normalize_audio_bit_depth,
    public_pcm_to_s16le,
    public_sample_endian,
    public_sample_format,
    public_sample_signed,
    resolve_audio_bit_depth,
    s16le_to_public_pcm,
    sample_width_bytes,
    silence_bytes,
)


def _pack_s16le(values):
    return b"".join(
        int(value).to_bytes(2, "little", signed=True) for value in values
    )


def _unpack_s16le(data):
    return [
        int.from_bytes(data[offset : offset + 2], "little", signed=True)
        for offset in range(0, len(data), 2)
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8, 8),
        (" 16 ", 16),
        ("24", 24),
        ("BEST", AUTO_PUBLIC_AUDIO_BIT_DEPTH),
    ],
)
def test_normalize_audio_bit_depth_accepts_public_options(value, expected):
    assert normalize_audio_bit_depth(value) == expected


@pytest.mark.parametrize("value", [7, 12, "auto", "24-bit", None, 1.0, True])
def test_normalize_audio_bit_depth_rejects_unknown_options(value):
    with pytest.raises(ValueError, match="audio_bit_depth"):
        normalize_audio_bit_depth(value)


def test_resolve_audio_bit_depth_uses_codec_preference_for_best():
    class CodecWithPreference:
        preferred_public_bit_depth = 16

    assert (
        resolve_audio_bit_depth(
            "best",
            codec=CodecWithPreference(),
            fallback=8,
        )
        == 16
    )

    assert (
        resolve_audio_bit_depth(
            "24",
            codec=CodecWithPreference(),
            fallback=8,
        )
        == 24
    )


@pytest.mark.parametrize(
    ("bit_depth", "width", "sample_format", "signed", "endian"),
    [
        (8, 1, "u8", False, None),
        (16, 2, "s16le", True, "little"),
        (24, 3, "s24le", True, "little"),
        (32, 4, "s32le", True, "little"),
        (64, 8, "s64le", True, "little"),
    ],
)
def test_public_sample_descriptors_match_bit_depth(
    bit_depth,
    width,
    sample_format,
    signed,
    endian,
):
    assert sample_width_bytes(bit_depth) == width
    assert public_sample_format(bit_depth) == sample_format
    assert public_sample_signed(bit_depth) is signed
    assert public_sample_endian(bit_depth) == endian


def test_public_audio_format_reports_legacy_u8_shape():
    fmt = PublicAudioFormat(sample_rate=8000, channels=1, bit_depth=8)

    assert fmt.sample_width == 1
    assert fmt.frame_size == 160

    details = fmt.as_dict()
    expected = {
        "sample_rate": 8000,
        "channels": 1,
        "bit_depth": 8,
        "bits_per_sample": 8,
        "sample_width": 1,
        "sample_width_bytes": 1,
        "encoding": "linear_pcm",
        "sample_format": "u8",
        "signed": False,
        "endian": None,
        "frame_ms": 20,
        "frame_size": 160,
    }
    for key, value in expected.items():
        assert details[key] == value


def test_public_audio_format_reports_stereo_s16_frame_shape():
    fmt = PublicAudioFormat(
        sample_rate=48000,
        channels=2,
        bit_depth=16,
        frame_ms=10,
    )

    assert fmt.sample_width == 2
    assert fmt.sample_format == "s16le"
    assert fmt.signed is True
    assert fmt.endian == "little"
    assert fmt.frame_size == 1920


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 0, "channels": 1, "bit_depth": 8},
        {"sample_rate": 8000, "channels": 0, "bit_depth": 8},
        {"sample_rate": 8000, "channels": 1, "bit_depth": 7},
        {"sample_rate": 8000, "channels": 1, "bit_depth": "best"},
        {"sample_rate": 8000, "channels": 1, "bit_depth": 8, "frame_ms": 0},
    ],
)
def test_public_audio_format_rejects_unusable_values(kwargs):
    with pytest.raises(ValueError):
        PublicAudioFormat(**kwargs)


def test_s16le_conversions_round_trip_through_wider_linear_formats():
    source = _pack_s16le([-32768, -1, 0, 1, 32767])

    for bit_depth in (16, 24, 32, 64):
        public = s16le_to_public_pcm(source, bit_depth)

        assert len(public) == 5 * sample_width_bytes(bit_depth)
        assert public_pcm_to_s16le(public, bit_depth) == source


def test_u8_public_pcm_uses_unsigned_midpoint_silence():
    source = _pack_s16le([-32768, 0, 32767])

    public = s16le_to_public_pcm(source, 8)

    assert public == bytes([0, 128, 255])
    assert _unpack_s16le(public_pcm_to_s16le(public, 8)) == [
        -32768,
        0,
        32512,
    ]


def test_pcm_conversions_ignore_incomplete_trailing_samples():
    assert public_pcm_to_s16le(b"\x00\x00\x00\x7f", 24) == _pack_s16le([0])
    assert s16le_to_public_pcm(b"\x00\x00\xff", 24) == b"\x00\x00\x00"


def test_silence_bytes_match_public_format_conventions():
    assert silence_bytes(3, 8) == b"\x80\x80\x80"
    assert silence_bytes(3, 16) == b"\x00\x00\x00"
    assert silence_bytes(-3, 16) == b""