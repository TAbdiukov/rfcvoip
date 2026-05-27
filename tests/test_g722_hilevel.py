import sys
import types

import pytest

from rfcvoip.RTP import PayloadType
from rfcvoip.codecs import create_codec
from rfcvoip.codecs import g722


@pytest.fixture(autouse=True)
def reset_g722_cache():
    g722.G722Codec.refresh_availability_cache()
    yield
    g722.G722Codec.refresh_availability_cache()


@pytest.fixture
def fake_g722_backend(monkeypatch):
    class FakeG722:
        instances = []

        def __init__(self, sample_rate, bit_rate, use_numpy=False):
            self.sample_rate = sample_rate
            self.bit_rate = bit_rate
            self.use_numpy = use_numpy
            self.encoded_inputs = []
            self.decoded_inputs = []
            type(self).instances.append(self)

        def encode(self, samples):
            samples = tuple(samples)
            self.encoded_inputs.append(samples)
            # G.722 at 64 kbps produces 160 bytes for 20 ms of 16 kHz audio.
            return b"\x55" * (len(samples) // 2)

        def decode(self, payload):
            payload = bytes(payload)
            self.decoded_inputs.append(payload)
            # 160 encoded bytes represent 320 decoded 16-bit samples.
            return [0] * (len(payload) * 2)

    module = types.ModuleType("G722")
    module.__version__ = "test"
    module.G722 = FakeG722
    monkeypatch.setitem(sys.modules, "G722", module)
    return FakeG722


def test_g722_reports_optional_backend_availability(fake_g722_backend):
    availability = g722.G722Codec.availability()

    assert availability.available is True
    assert availability.library == "G722 test"
    assert availability.reason == "G722 encoder/decoder available"

    probe = fake_g722_backend.instances[-1]
    assert probe.sample_rate == 16000
    assert probe.bit_rate == 64000
    assert probe.use_numpy is False


def test_codec_registry_creates_g722_with_expected_public_audio_format(
    fake_g722_backend,
):
    codec = create_codec(
        PayloadType.G722,
        source_sample_rate=16000,
        source_bit_depth=16,
        source_channels=1,
    )

    # RFC 3551 uses an 8 kHz RTP timestamp clock for G.722 even though the
    # codec operates on 16 kHz audio.
    assert codec.rate == 8000
    assert codec.codec_sample_rate == 16000
    assert codec.source_sample_rate == 16000
    assert codec.source_sample_width == 2
    assert codec.source_frame_size() == 640
    assert codec.rtp_timestamp_increment(b"\x00" * 640, b"\x55" * 160) == 160


def test_encode_pads_public_pcm_to_one_twenty_ms_g722_frame(
    fake_g722_backend,
):
    codec = create_codec(
        PayloadType.G722,
        source_sample_rate=16000,
        source_bit_depth=16,
        source_channels=1,
    )

    encoded = codec.encode(b"\x00\x00" * 8)

    assert encoded == b"\x55" * 160
    assert len(codec._encoder.encoded_inputs[-1]) == 320
    assert codec._encoder.encoded_inputs[-1][:8] == (0,) * 8


def test_decode_expands_one_twenty_ms_g722_payload_to_public_pcm_frame(
    fake_g722_backend,
):
    codec = create_codec(
        PayloadType.G722,
        source_sample_rate=16000,
        source_bit_depth=16,
        source_channels=1,
    )

    decoded = codec.decode(b"\x99" * 160)

    assert decoded == b"\x00\x00" * 320
    assert codec._decoder.decoded_inputs[-1] == b"\x99" * 160


def test_empty_decode_returns_silence_for_configured_public_format(
    fake_g722_backend,
):
    eight_bit = create_codec(
        PayloadType.G722,
        source_sample_rate=16000,
        source_bit_depth=8,
        source_channels=1,
    )
    sixteen_bit = create_codec(
        PayloadType.G722,
        source_sample_rate=16000,
        source_bit_depth=16,
        source_channels=1,
    )

    assert eight_bit.decode(b"") == b"\x80" * 320
    assert sixteen_bit.decode(b"") == b"\x00" * 640


def test_g722_reports_unavailable_when_optional_backend_cannot_import(
    monkeypatch,
):
    real_import_module = g722.importlib.import_module
    monkeypatch.delitem(sys.modules, "G722", raising=False)

    def import_module(name, *args, **kwargs):
        if name == "G722":
            raise ImportError("no module named G722")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(g722.importlib, "import_module", import_module)
    g722.G722Codec.refresh_availability_cache()

    availability = g722.G722Codec.availability()

    assert availability.available is False
    assert "G722 package is not available" in availability.reason