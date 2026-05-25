import pytest

from rfcvoip import RTP, Telemetry
from rfcvoip.audio_format import (
    PublicAudioFormat,
    public_pcm_to_s16le,
    s16le_to_public_pcm,
)
from rfcvoip.VoIP import VoIPPhone


def make_phone(**kwargs):
    return VoIPPhone(
        "sip.example.net",
        5060,
        "1000",
        "password",
        myIP="192.0.2.10",
        **kwargs,
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        (8, 8),
        ("8", 8),
        (16, 16),
        ("16", 16),
        (24, 24),
        ("24", 24),
        (32, 32),
        (64, 64),
        ("best", "best"),
        ("BEST", "best"),
    ],
)
def test_audio_bit_depth_constructor_accepts_valid_values(value, expected):
    phone = make_phone(audio_bit_depth=value)
    assert phone.audio_bit_depth == expected


@pytest.mark.parametrize(
    "value",
    [0, 12, 20, 48, "float32", "s16le", object()],
)
def test_audio_bit_depth_constructor_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        make_phone(audio_bit_depth=value)


@pytest.mark.parametrize(
    "bits, expected",
    [
        (8, 160),
        (16, 320),
        (24, 480),
        (32, 640),
        (64, 1280),
    ],
)
def test_8k_mono_20ms_frame_sizes(bits, expected):
    fmt = PublicAudioFormat(sample_rate=8000, channels=1, bit_depth=bits)
    assert fmt.frame_size == expected


@pytest.mark.parametrize(
    "bits, expected",
    [
        (8, 1920),
        (16, 3840),
        (24, 5760),
        (32, 7680),
        (64, 15360),
    ],
)
def test_48k_stereo_20ms_frame_sizes(bits, expected):
    fmt = PublicAudioFormat(sample_rate=48000, channels=2, bit_depth=bits)
    assert fmt.frame_size == expected


def _pcm16(values):
    out = bytearray()
    for value in values:
        out.extend(int(value).to_bytes(2, "little", signed=True))
    return bytes(out)


def _pcm16_values(data):
    return [
        int.from_bytes(data[i : i + 2], "little", signed=True)
        for i in range(0, len(data), 2)
    ]


def test_16_24_32_64_roundtrip_exact_to_s16():
    pcm16 = _pcm16([-32768, -12345, -1, 0, 1, 12345, 32767])

    for bits in (16, 24, 32, 64):
        public = s16le_to_public_pcm(pcm16, bits)
        assert public_pcm_to_s16le(public, bits) == pcm16


def test_8_bit_roundtrip_is_legacy_unsigned_quantized():
    pcm16 = _pcm16([-32768, -1, 0, 1, 32767])
    public = s16le_to_public_pcm(pcm16, 8)
    out = _pcm16_values(public_pcm_to_s16le(public, 8))
    assert out == [-32768, -256, 0, 0, 32512]


def test_phone_audio_format_reports_fixed_bit_depth():
    phone = make_phone(
        audio_sample_rate=8000,
        audio_channels=1,
        audio_bit_depth=24,
    )
    fmt = phone.audio_format()

    assert fmt["sample_rate"] == 8000
    assert fmt["channels"] == 1
    assert fmt["bit_depth"] == 24
    assert fmt["bits_per_sample"] == 24
    assert fmt["sample_width"] == 3
    assert fmt["frame_size"] == 480
    assert fmt["sample_format"] == "s24le"


def test_best_uses_fallback_before_negotiation():
    phone = make_phone(audio_bit_depth="best")
    assert phone.audio_format()["bit_depth"] == 8


def test_best_uses_selected_codec_preferred_bit_depth_after_negotiation():
    client = RTP.RTPClient(
        {112: RTP.PayloadType.PCMU_WB},
        "127.0.0.1",
        40000,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
        audio_bit_depth="best",
    )
    assert client.audio_bit_depth == 16
    assert client.audio_format()["bit_depth"] == 16


class FakeRTPClient:
    preference = RTP.PayloadType.PCMU
    preference_payload_type = 0
    inIP = "127.0.0.1"
    inPort = 40000
    outIP = "127.0.0.1"
    outPort = 40002
    sendrecv = RTP.TransmitType.SENDRECV
    audio_sample_rate = 8000
    audio_sample_width = 4
    audio_bit_depth = 32
    audio_channels = 1

    def audio_format(self, duration_ms=20):
        return PublicAudioFormat(
            sample_rate=self.audio_sample_rate,
            channels=self.audio_channels,
            bit_depth=self.audio_bit_depth,
            frame_ms=duration_ms,
        ).as_dict()


class FakeCall:
    call_id = "call-id"
    state = "ANSWERED"
    session_id = "1"
    sendmode = RTP.TransmitType.SENDRECV
    assignedPorts = {40000: {0: RTP.PayloadType.PCMU}}
    remote_sip_message = None
    sip = None

    def __init__(self):
        self.RTPClients = [FakeRTPClient()]

    def _rtp_clients_snapshot(self):
        return list(self.RTPClients)

    def audio_format(self):
        return self.RTPClients[0].audio_format()


def test_telemetry_exposes_public_audio_bit_depth():
    call = FakeCall()

    assert Telemetry.get(call, "audio.bit_depth") == 32
    assert Telemetry.get(call, "audio.bits_per_sample") == 32
    assert Telemetry.get(call, "public_audio.bit_depth") == 32
    assert Telemetry.get(call, "public_audio.frame_size") == 640
    assert Telemetry.get(call, "media.audio.bit_depth") == 32
    assert Telemetry.get(call, "rtp.audio.bit_depth") == 32

    active = Telemetry.call_active_codecs(call)
    assert active
    assert active[0]["public_audio_bit_depth"] == 32
    assert active[0]["public_audio_format"]["sample_format"] == "s32le"


def test_default_audio_bit_depth_is_legacy_8_bit():
    phone = make_phone()
    fmt = phone.audio_format()
    assert fmt["bit_depth"] == 8
    assert fmt["sample_format"] == "u8"
    assert phone.public_audio_frame_size() == 160