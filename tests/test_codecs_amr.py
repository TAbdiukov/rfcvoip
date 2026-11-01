"""RFC 4867 framing and the OpenCORE/VisualOn boundary, without native deps."""

import os
from types import SimpleNamespace

import pytest

from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs import amr

# RFC 4867 / 3GPP IF1 speech bit counts. Keep these independent of the adapter.
NB_BITS = (95, 103, 118, 134, 148, 159, 204, 244, 39)
WB_BITS = (132, 177, 253, 285, 317, 365, 397, 461, 477, 40)
CODECS = (amr.AMRCodec, amr.AMRWBCodec)


def speech_bytes(bits):
    result = bytearray(b"\xa5" * ((bits + 7) // 8))
    if bits % 8:
        result[-1] &= 0xFF << (8 - bits % 8)
    return bytes(result)


def be_packet(frames, bit_counts, cmr=15):
    """Construct an RFC packet directly as bits, independently of production."""
    bits = f"{cmr:04b}"
    for index, (ft, quality, _body) in enumerate(frames):
        bits += f"{int(index + 1 < len(frames)):01b}{ft:04b}{quality:01b}"
    for ft, _quality, body in frames:
        count = bit_counts[ft] if ft < len(bit_counts) else 0
        bits += "".join(f"{value:08b}" for value in body)[:count]
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


class NativeAPI:
    def __init__(self, wideband):
        self.wideband = wideband
        self.bits = WB_BITS if wideband else NB_BITS
        self.samples = 320 if wideband else 160
        self.encoded = []
        self.decoded = []
        self.destroyed = []

    def encode(self, state, mode, pcm, output, _flag):
        self.encoded.append((state, mode, tuple(pcm)))
        frame = bytes([(mode << 3) | 4]) + speech_bytes(self.bits[mode])
        for index, value in enumerate(frame):
            output[index] = value
        return len(frame)

    def decode(self, state, frame, pcm, bad_frame):
        ft = (frame[0] >> 3) & 15
        size = 1 + ((self.bits[ft] + 7) // 8 if ft < len(self.bits) else 0)
        self.decoded.append((state, bytes(frame[:size]), bad_frame))
        for index in range(self.samples):
            pcm[index] = 0x1234 if index % 2 == 0 else -0x1234

    def libraries(self):
        if self.wideband:
            return (
                SimpleNamespace(
                    E_IF_init=lambda: 11,
                    E_IF_encode=self.encode,
                    E_IF_exit=lambda state: self.destroyed.append(
                        ("e", state)
                    ),
                ),
                SimpleNamespace(
                    D_IF_init=lambda: 22,
                    D_IF_decode=self.decode,
                    D_IF_exit=lambda state: self.destroyed.append(
                        ("d", state)
                    ),
                ),
            )
        backend = SimpleNamespace(
            Encoder_Interface_init=lambda _dtx: 11,
            Encoder_Interface_Encode=self.encode,
            Encoder_Interface_exit=lambda state: self.destroyed.append(
                ("e", state)
            ),
            Decoder_Interface_init=lambda: 22,
            Decoder_Interface_Decode=self.decode,
            Decoder_Interface_exit=lambda state: self.destroyed.append(
                ("d", state)
            ),
        )
        return backend, backend


@pytest.fixture(params=CODECS, ids=("AMR", "AMR-WB"))
def native_codec(request, monkeypatch):
    cls = request.param
    api = NativeAPI(cls is amr.AMRWBCodec)
    monkeypatch.setattr(
        amr, "_get_libraries", lambda _wideband: api.libraries()
    )
    codec = cls()
    codec.configure_source_format(sample_rate=codec.rate, bit_depth=16)
    yield codec, api
    codec.close()


def test_octet_aligned_encode_matches_rfc_header_and_if1_bits(native_codec):
    codec, api = native_codec
    mode = 8 if api.wideband else 7
    source = bytes(codec.source_frame_size())

    packet = codec.encode(source)

    assert packet == bytes([0xF0, (mode << 3) | 4]) + speech_bytes(
        api.bits[mode]
    )
    assert len(api.encoded) == 1
    assert api.encoded[0][1] == mode
    assert len(api.encoded[0][2]) == api.samples
    assert set(api.encoded[0][2]) == {0}
    assert codec.rtp_timestamp_increment(source, packet) == api.samples


def test_bandwidth_efficient_encode_matches_independent_bit_vector(
    native_codec,
):
    codec, api = native_codec
    codec.configure_fmtp(["octet-align=0;mode-set=0"])

    packet = codec.encode(bytes(codec.source_frame_size()))

    assert packet == be_packet([(0, 1, speech_bytes(api.bits[0]))], api.bits)
    assert packet[0] == 0xF0  # 1111 CMR, 0 F, 000 FT high bits
    assert api.encoded[0][1] == 0


@pytest.mark.parametrize("cls", CODECS)
def test_fmtp_default_and_explicit_modes_follow_rfc_defaults(cls):
    modes = tuple(range(9 if cls is amr.AMRWBCodec else 8))
    assert cls.fmtp_parameters([]) == {
        "octet-align": False,
        "mode-set": modes,
        "mode-change-period": 1,
        "mode-change-neighbor": False,
    }
    assert cls.fmtp_supported(
        [
            "octet-align=1;mode-set=2,0;mode-change-period=2;mode-change-neighbor=1"
        ]
    )
    assert cls.fmtp_parameters(["mode-set=2,0"])["mode-set"] == (0, 2)
    assert cls.fmtp_supported(["crc=0;robust-sorting=0;vendor-extension=yes"])


@pytest.mark.parametrize("cls", CODECS)
@pytest.mark.parametrize(
    "settings",
    (
        "octet-align=2",
        "octet-align=invalid",
        "mode-set=",
        "mode-set=0,16",
        "mode-set=-1",
        "mode-set=not-a-mode",
        "crc=1",
        "robust-sorting=1",
        "interleaving=0",
        "interleaving=2",
        "mode-change-period=3",
        "mode-change-neighbor=2",
    ),
)
def test_unsupported_fmtp_cannot_be_negotiated(cls, settings):
    assert not cls.fmtp_supported([settings])
    with pytest.raises(ValueError):
        cls.fmtp_parameters([settings])


def test_fmtp_mode_set_restricts_transmitter_and_receiver(native_codec):
    codec, api = native_codec
    codec.configure_fmtp(["octet-align=1;mode-set=0,2"])
    codec.encode(bytes(codec.source_frame_size()))

    assert api.encoded[0][1] == 2
    with pytest.raises(ValueError):
        codec.decode(b"\xf0\x0c" + speech_bytes(api.bits[1]))
    assert api.decoded == []


def test_cmr_requests_follow_negotiated_neighbor_and_change_period(
    native_codec,
):
    codec, api = native_codec
    codec.configure_fmtp(
        [
            "octet-align=1;mode-set=0,2,4;mode-change-neighbor=1;mode-change-period=2"
        ]
    )
    source = bytes(codec.source_frame_size())
    codec.encode(source)
    codec.request_mode(0)
    for _ in range(4):
        codec.encode(source)

    assert [entry[1] for entry in api.encoded] == [4, 4, 2, 2, 0]


def test_cmr_outside_active_set_is_ignored_and_fifteen_cancels(native_codec):
    codec, api = native_codec
    codec.configure_fmtp(["octet-align=1;mode-set=0,2"])
    source = bytes(codec.source_frame_size())
    codec.request_mode(0)
    codec.encode(source)
    codec.request_mode(1)
    codec.encode(source)
    codec.request_mode(15)
    codec.encode(source)

    assert [entry[1] for entry in api.encoded] == [0, 0, 2]


@pytest.mark.parametrize("octet_align", (False, True))
def test_aggregated_payload_preserves_frame_order_and_duration(
    native_codec, octet_align
):
    codec, api = native_codec
    codec.configure_fmtp([f"octet-align={int(octet_align)};mode-set=0"])
    source = bytes(codec.source_frame_size(40))

    packet = codec.encode(source)
    decoded = codec.decode(packet)

    body = speech_bytes(api.bits[0])
    expected = (
        bytes([0xF0, 0x84, 0x04]) + body + body
        if octet_align
        else be_packet([(0, 1, body), (0, 1, body)], api.bits)
    )
    assert packet == expected
    assert [entry[1] for entry in api.decoded] == [b"\x04" + body] * 2
    assert len(decoded) == codec.source_frame_size(40)
    assert codec.packet_duration_seconds(source) == pytest.approx(0.04)
    assert codec.rtp_timestamp_increment(source, packet) == api.samples * 2


@pytest.mark.parametrize("octet_align", (False, True))
def test_decode_sid_and_no_data_uses_native_comfort_noise(
    native_codec, octet_align
):
    codec, api = native_codec
    codec.configure_fmtp([f"octet-align={int(octet_align)}"])
    sid = 9 if api.wideband else 8
    body = speech_bytes(api.bits[sid])
    frames = [(sid, 1, body), (15, 1, b"")]
    packet = (
        bytes([0xF0, 0x80 | (sid << 3) | 4, 0x7C]) + body
        if octet_align
        else be_packet(frames, api.bits)
    )

    assert len(codec.decode(packet)) == codec.source_frame_size(40)
    assert [entry[1] for entry in api.decoded] == [
        bytes([(sid << 3) | 4]) + body,
        b"\x7c",
    ]


def test_decode_bad_quality_propagates_bad_frame_indicator(native_codec):
    codec, api = native_codec
    body = speech_bytes(api.bits[0])

    codec.decode(b"\xf0\x00" + body)

    assert len(api.decoded) == 1
    assert api.decoded[0][2] == 1


@pytest.mark.parametrize(
    "packet", (b"\xf0", b"\xf0\x84", b"\xf0\x04", b"\xf0\x6c")
)
def test_malformed_octet_packets_never_reach_native_decoder(
    native_codec, packet
):
    codec, api = native_codec

    with pytest.raises(ValueError):
        codec.decode(packet)

    assert api.decoded == []


@pytest.mark.parametrize("octet_align", (False, True))
def test_truncated_second_frame_is_rejected_before_decoding_first(
    native_codec, octet_align
):
    codec, api = native_codec
    codec.configure_fmtp([f"octet-align={int(octet_align)}"])
    body = speech_bytes(api.bits[0])
    packet = (
        bytes([0xF0, 0x84, 0x04]) + body + body
        if octet_align
        else be_packet([(0, 1, body), (0, 1, body)], api.bits)
    )

    with pytest.raises(ValueError):
        codec.decode(packet[:-1])

    assert api.decoded == []


@pytest.mark.parametrize("bit_depth", (8, 16, 24, 32, 64))
@pytest.mark.parametrize("channels", (1, 2))
def test_public_pcm_formats_convert_to_native_mono_and_back(
    native_codec, bit_depth, channels
):
    codec, api = native_codec
    codec.configure_source_format(
        sample_rate=codec.rate, bit_depth=bit_depth, channels=channels
    )
    frame = silence_bytes(codec.source_frame_size(), bit_depth)

    packet = codec.encode(frame)
    decoded = codec.decode(packet)

    assert len(api.encoded[0][2]) == api.samples
    assert set(api.encoded[0][2]) == {0}
    assert len(decoded) == len(frame)
    if bit_depth == 16:
        assert decoded[: 4 * channels] == (
            b"\x34\x12" * channels + b"\xcc\xed" * channels
        )


def test_twenty_ms_timestamp_survives_fractional_public_rate(native_codec):
    codec, api = native_codec
    codec.configure_source_format(sample_rate=11025, bit_depth=16, channels=2)
    source = bytes(codec.source_frame_size(20))

    period = codec.packet_duration_seconds(source)
    packet = codec.encode(source)

    assert len(api.encoded) == 1
    assert len(api.encoded[0][2]) == api.samples
    assert period == pytest.approx(0.02)
    assert codec.rtp_timestamp_increment(source, packet) == api.samples


def test_close_is_idempotent_and_releases_encoder_and_decoder(native_codec):
    codec, api = native_codec
    packet = codec.encode(bytes(codec.source_frame_size()))
    codec.decode(packet)

    codec.close()
    codec.close()

    assert sorted(api.destroyed) == [("d", 22), ("e", 11)]


@pytest.mark.parametrize(
    "cls,mode",
    [
        (cls, mode)
        for cls in CODECS
        for mode in range(9 if cls is amr.AMRWBCodec else 8)
    ],
    ids=[
        f"{cls.name}-mode{mode}"
        for cls in CODECS
        for mode in range(9 if cls is amr.AMRWBCodec else 8)
    ],
)
@pytest.mark.parametrize("octet_align", (False, True), ids=("BE", "OA"))
@pytest.mark.parametrize("duration", (20, 40), ids=("single", "aggregate"))
def test_native_libraries_encode_decode_real_audio_when_installed(
    cls, mode, octet_align, duration
):
    availability = cls.availability()
    if not availability.available:
        if os.environ.get("RFCVOIP_REQUIRE_AMR") == "1":
            pytest.fail(f"Native {cls.name} required: {availability.reason}")
        pytest.skip(f"Native {cls.name} unavailable: {availability.reason}")
    codec = cls()
    try:
        codec.configure_source_format(sample_rate=codec.rate, bit_depth=16)
        # A nonconstant waveform exercises the native speech encoders.
        source = b"".join(
            (12000 if index % 40 < 20 else -12000).to_bytes(
                2, "little", signed=True
            )
            for index in range(codec.rate * duration // 1000)
        )
        codec.configure_fmtp(
            [f"octet-align={int(octet_align)};mode-set={mode}"]
        )
        packet = codec.encode(source)
        frame_count = duration // 20
        bit_count = (WB_BITS if cls is amr.AMRWBCodec else NB_BITS)[mode]
        expected_size = (
            1 + frame_count * (1 + (bit_count + 7) // 8)
            if octet_align
            else (4 + frame_count * (6 + bit_count) + 7) // 8
        )
        assert len(packet) == expected_size
        decoded = codec.decode(packet)
        assert len(decoded) == len(source)
        assert any(decoded)
        assert (
            codec.rtp_timestamp_increment(source, packet)
            == codec.rate * duration // 1000
        )
    finally:
        codec.close()
