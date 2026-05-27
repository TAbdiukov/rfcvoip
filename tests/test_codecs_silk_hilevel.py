import struct
import sys
import types

import pytest

from rfcvoip.audio_format import silence_bytes
import rfcvoip.codecs.silk as silk


@pytest.fixture
def fake_pysilk(monkeypatch):
    calls = {"encode": [], "decode": []}
    module = types.SimpleNamespace(
        __name__="pysilk",
        __version__="9.9-test",
        encoded_frame=b"\x11\x22silk-frame",
        decoded_pcm16=b"\x34\x12" * 160,
    )

    def encode(source, encoded, sample_rate, bit_rate, **kwargs):
        calls["encode"].append(
            {
                "pcm16": source.read(),
                "sample_rate": sample_rate,
                "bit_rate": bit_rate,
                "kwargs": kwargs,
            }
        )
        frame = module.encoded_frame
        encoded.write(
            silk._SILK_STORAGE_HEADER
            + struct.pack("<h", len(frame))
            + frame
        )

    def decode(source, decoded, sample_rate, frame_size, frames_per_packet):
        calls["decode"].append(
            {
                "storage": source.read(),
                "sample_rate": sample_rate,
                "frame_size": frame_size,
                "frames_per_packet": frames_per_packet,
            }
        )
        decoded.write(module.decoded_pcm16)

    module.encode = encode
    module.decode = decode

    monkeypatch.setitem(sys.modules, "pysilk", module)
    silk.SilkCodec.refresh_availability_cache()
    yield module, calls
    silk.SilkCodec.refresh_availability_cache()


def test_availability_reports_the_optional_pysilk_module(fake_pysilk):
    availability = silk.Silk8000Codec.availability()

    assert availability.available is True
    assert availability.reason == "pysilk SILK encoder/decoder available"
    assert availability.library == "pysilk 9.9-test"


def test_encode_returns_an_rtp_silk_frame_not_a_storage_container(fake_pysilk):
    module, calls = fake_pysilk
    codec = silk.Silk8000Codec()
    codec.configure_source_format(sample_rate=8000, bit_depth=16, channels=1)
    public_pcm16 = b"\x01\x00" * 160

    rtp_payload = codec.encode(public_pcm16)

    assert rtp_payload == module.encoded_frame
    assert not rtp_payload.startswith(silk._SILK_STORAGE_HEADER)
    assert calls["encode"] == [
        {
            "pcm16": public_pcm16,
            "sample_rate": 8000,
            "bit_rate": 12000,
            "kwargs": {
                "max_internal_sample_rate": 8000,
                "packet_loss_percentage": 0,
                "complexity": 2,
                "use_inband_fec": False,
                "use_dtx": False,
                "tencent": False,
            },
        }
    ]


def test_decode_wraps_rtp_payload_as_silk_storage_for_pysilk(fake_pysilk):
    module, calls = fake_pysilk
    codec = silk.Silk8000Codec()
    codec.configure_source_format(sample_rate=8000, bit_depth=16, channels=1)
    rtp_payload = b"\xaa\xbb\xcc"

    public_pcm = codec.decode(rtp_payload)

    assert public_pcm == module.decoded_pcm16
    assert calls["decode"] == [
        {
            "storage": (
                silk._SILK_STORAGE_HEADER
                + struct.pack("<h", len(rtp_payload))
                + rtp_payload
            ),
            "sample_rate": 8000,
            "frame_size": 160,
            "frames_per_packet": 1,
        }
    ]


def test_empty_decode_returns_public_silence_without_calling_pysilk(fake_pysilk):
    _module, calls = fake_pysilk
    codec = silk.Silk16000Codec()
    codec.configure_source_format(sample_rate=16000, bit_depth=16, channels=1)

    public_pcm = codec.decode(b"")

    assert public_pcm == silence_bytes(codec.source_frame_size(), 16)
    assert calls["decode"] == []


@pytest.mark.parametrize(
    ("codec_cls", "default_payload_type", "rate", "bit_rate"),
    [
        (silk.Silk24000Codec, 114, 24000, 24000),
        (silk.Silk16000Codec, 115, 16000, 20000),
        (silk.Silk12000Codec, 116, 12000, 16000),
        (silk.Silk8000Codec, 117, 8000, 12000),
    ],
)
def test_silk_variants_advertise_their_rtp_metadata(
    codec_cls,
    default_payload_type,
    rate,
    bit_rate,
):
    assert codec_cls.default_payload_type == default_payload_type
    assert codec_cls.rate == rate
    assert codec_cls.preferred_source_sample_rate == rate
    assert codec_cls.required_bandwidth_bps == bit_rate
    assert codec_cls.rtpmap(default_payload_type) == (
        f"{default_payload_type} SILK/{rate}"
    )


@pytest.mark.parametrize(
    ("codec_cls", "fmtp", "expected"),
    [
        (silk.Silk8000Codec, [], True),
        (silk.Silk8000Codec, ["usedtx=1;maxaveragebitrate=12000"], True),
        (silk.Silk8000Codec, ["usedtx=0"], True),
        (silk.Silk8000Codec, ["usedtx=2"], False),
        (silk.Silk16000Codec, ["maxaveragebitrate=7999"], False),
        (silk.Silk16000Codec, ["maxaveragebitrate=19999"], False),
        (silk.Silk16000Codec, ["maxaveragebitrate=20000"], True),
        (silk.Silk16000Codec, ["maxaveragebitrate=not-an-int"], False),
    ],
)
def test_fmtp_support_matches_the_encoder_constraints(
    codec_cls,
    fmtp,
    expected,
):
    assert codec_cls.fmtp_supported(fmtp) is expected