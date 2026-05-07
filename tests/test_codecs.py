import pyVoIP

from pyVoIP import RTP
from pyVoIP.codecs import create_codec


def test_supported_codecs_include_builtin_g711_and_telephone_event():
    codecs = pyVoIP.supported_codecs()
    names = {codec["name"] for codec in codecs}

    assert {"PCMU", "PCMA", "telephone-event"} <= names


def test_pcmu_codec_roundtrip_preserves_public_frame_length():
    codec = create_codec(
        RTP.PayloadType.PCMU,
        source_sample_rate=8000,
        source_sample_width=1,
        source_channels=1,
    )

    frame = b"\x80" * codec.source_frame_size()
    encoded = codec.encode(frame)
    decoded = codec.decode(encoded)

    assert len(encoded) == len(frame)
    assert len(decoded) == len(frame)