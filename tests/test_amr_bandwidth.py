"""AMR bandwidth negotiation accounts for the active speech mode set."""

import pytest

import rfcvoip
from rfcvoip import RTP, Telemetry, codecs
from rfcvoip.codecs.amr import AMRCodec, AMRWBCodec
from rfcvoip.codecs.base import CodecAvailability
from rfcvoip.SIP import SIPMessage

NB_RATES = (4750, 5150, 5900, 6700, 7400, 7950, 10200, 12200)
WB_RATES = (6600, 8850, 12650, 14250, 15850, 18250, 19850, 23050, 23850)
AMR_CODECS = (
    (RTP.PayloadType.AMR, AMRCodec, NB_RATES),
    (RTP.PayloadType.AMR_WB, AMRWBCodec, WB_RATES),
)


@pytest.fixture(autouse=True)
def native_amr_available(monkeypatch):
    for cls in (AMRCodec, AMRWBCodec):
        monkeypatch.setattr(
            cls,
            "availability",
            classmethod(
                lambda cls: CodecAvailability(True, "test native backend")
            ),
        )
    monkeypatch.setattr(
        rfcvoip,
        "RTPCompatibleCodecs",
        [
            RTP.PayloadType.AMR,
            RTP.PayloadType.AMR_WB,
            RTP.PayloadType.PCMU,
            RTP.PayloadType.PCMA,
            RTP.PayloadType.EVENT,
        ],
    )


def offer(codec, settings, cap, session_cap=None):
    payload = RTP.default_payload_type(codec)
    lines = [
        "v=0",
        "o=- 1 1 IN IP4 192.0.2.20",
        "s=AMR bandwidth",
        "c=IN IP4 192.0.2.20",
        "t=0 0",
    ]
    if session_cap is not None:
        lines.append(f"b=AS:{session_cap}")
    lines.extend(
        [
            f"m=audio 4000 RTP/AVP {payload}",
            f"b=TIAS:{cap}",
            f"a=rtpmap:{payload} {codec}/{codec.rate}",
            f"a=fmtp:{payload} {settings}",
        ]
    )
    body = "\r\n".join(lines) + "\r\n"
    packet = (
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK-bandwidth\r\n"
        "From: <sip:alice@example.com>;tag=alice\r\n"
        "To: <sip:bob@example.com>;tag=bob\r\n"
        "Call-ID: amr-bandwidth@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    )
    return SIPMessage(packet.encode("ascii"))


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
def test_bandwidth_metadata_stays_at_full_rate_without_fmtp(codec, cls, rates):
    assert codecs.codec_required_bandwidth_bps(codec) == rates[-1]
    assert codecs.codec_required_bandwidth_bps(codec, fmtp=[]) == rates[-1]
    assert cls.negotiated_bandwidth_bps([]) == rates[-1]
    assert cls.negotiated_bandwidth_bps(["mode-set=0"]) == rates[0]
    assert codecs.codec_required_bandwidth_bps(codec) == rates[-1]
    assert (
        codecs.codec_availability(codec)["required_bandwidth_bps"] == rates[-1]
    )
    assert Telemetry.codec_info(codec)["required_bandwidth_bps"] == rates[-1]


@pytest.mark.parametrize(
    "codec,mode,expected",
    [
        (codec, mode, bitrate)
        for codec, _cls, rates in AMR_CODECS
        for mode, bitrate in enumerate(rates)
    ],
)
def test_each_negotiated_mode_reports_its_standard_speech_rate(
    codec, mode, expected
):
    settings = [f"octet-align=1;mode-set={mode}"]

    assert (
        codecs.codec_required_bandwidth_bps(codec, fmtp=settings) == expected
    )


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
@pytest.mark.parametrize("octet_align", (0, 1))
def test_subset_uses_highest_allowed_mode_independent_of_packet_format(
    codec, cls, rates, octet_align
):
    settings = [f"mode-set=2,0;octet-align={octet_align}"]

    assert cls.negotiated_bandwidth_bps(settings) == rates[2]
    assert (
        codecs.codec_required_bandwidth_bps(codec, fmtp=settings) == rates[2]
    )
    all_modes = ",".join(str(mode) for mode in reversed(range(len(rates))))
    assert cls.negotiated_bandwidth_bps([f"mode-set={all_modes}"]) == rates[-1]


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
@pytest.mark.parametrize("mode_set,index", (("0", 0), ("2,0", 2)))
def test_low_mode_offer_is_supported_at_negotiated_cap(
    codec, cls, rates, mode_set, index
):
    required = rates[index]
    message = offer(codec, f"octet-align=1;mode-set={mode_set}", required)

    records = Telemetry.sip_supported_codecs(message)

    assert len(records) == 1
    info = records[0]
    assert info["required_bandwidth_bps"] == required
    assert info["bandwidth"]["required_bps"] == required
    assert info["bandwidth"]["limit_bps"] == required
    assert info["bandwidth_supported"] is True
    assert info["fmtp_supported"] is True
    assert info["supported"] is True
    assert Telemetry.codec_support_report(message)["can_start_call"] is True


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
@pytest.mark.parametrize("mode_set,index", (("0", 0), ("0,2", 2)))
def test_cap_below_highest_active_mode_makes_offer_unsupported(
    codec, cls, rates, mode_set, index
):
    required = rates[index]
    message = offer(codec, f"octet-align=0;mode-set={mode_set}", required - 1)

    info = Telemetry.sip_supported_codecs(message)[0]

    assert info["required_bandwidth_bps"] == required
    assert info["bandwidth"]["required_bps"] == required
    assert info["bandwidth_supported"] is False
    assert info["fmtp_supported"] is True
    assert info["supported"] is False
    assert Telemetry.codec_support_report(message)["can_start_call"] is False


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
def test_session_and_media_caps_both_apply_to_active_mode(codec, cls, rates):
    required = rates[0]
    enough_kbps = (required + 999) // 1000
    accepted = offer(codec, "mode-set=0", required, session_cap=enough_kbps)
    rejected = offer(
        codec, "mode-set=0", required, session_cap=enough_kbps - 1
    )

    assert Telemetry.sip_supported_codecs(accepted)[0]["supported"] is True
    info = Telemetry.sip_supported_codecs(rejected)[0]
    assert info["bandwidth"]["limit_bps"] == (enough_kbps - 1) * 1000
    assert info["bandwidth_supported"] is False
    assert info["supported"] is False


@pytest.mark.parametrize("codec,cls,rates", AMR_CODECS)
@pytest.mark.parametrize(
    "settings", ("mode-set=99", "mode-set=", "mode-set=0;crc=1")
)
def test_invalid_fmtp_stays_unsupported_despite_generous_bandwidth(
    codec, cls, rates, settings
):
    with pytest.raises(ValueError):
        codecs.codec_required_bandwidth_bps(codec, fmtp=[settings])
    message = offer(codec, settings, 100000)

    info = Telemetry.sip_supported_codecs(message)[0]

    assert info["available"] is True
    assert info["fmtp_supported"] is False
    assert info["supported"] is False
    assert Telemetry.codec_support_report(message)["can_start_call"] is False


@pytest.mark.parametrize(
    "codec,required",
    (
        (RTP.PayloadType.PCMU, 64000),
        (RTP.PayloadType.PCMA, 64000),
        (RTP.PayloadType.G722, 64000),
        (RTP.PayloadType.EVENT, None),
        (RTP.PayloadType.GSM, None),
    ),
)
def test_amr_specific_fmtp_does_not_change_other_codec_bandwidth(
    codec, required
):
    assert codecs.codec_required_bandwidth_bps(codec) == required
    assert (
        codecs.codec_required_bandwidth_bps(codec, fmtp=["mode-set=0"])
        == required
    )


def test_existing_g711_telemetry_bandwidth_checks_remain_unchanged():
    accepted = Telemetry.sip_supported_codecs(
        offer(RTP.PayloadType.PCMU, "", 64000)
    )[0]
    rejected = Telemetry.sip_supported_codecs(
        offer(RTP.PayloadType.PCMU, "", 63999)
    )[0]

    assert accepted["required_bandwidth_bps"] == 64000
    assert accepted["supported"] is True
    assert rejected["required_bandwidth_bps"] == 64000
    assert rejected["bandwidth_supported"] is False
    assert rejected["supported"] is False
