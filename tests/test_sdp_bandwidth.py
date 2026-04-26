from pyVoIP.SIP import SIPMessage
from pyVoIP.VoIP import VoIPPhone


def _invite_with_sdp(*sdp_lines: str) -> SIPMessage:
    body = "\r\n".join(sdp_lines) + "\r\n"
    packet = (
        "INVITE sip:bob@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 198.51.100.10:5060;branch=z9hG4bKtest;rport\r\n"
        "From: <sip:alice@example.com>;tag=fromtag\r\n"
        "To: <sip:bob@example.com>\r\n"
        "Call-ID: bandwidth-test@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:alice@198.51.100.10:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        + body
    )
    return SIPMessage(packet.encode("utf8"))


def _basic_sdp(*extra_lines: str):
    return (
        "v=0",
        "o=- 1 2 IN IP4 203.0.113.20",
        "s=-",
        "c=IN IP4 203.0.113.20",
        "t=0 0",
        *extra_lines,
    )


def _pcmu_from_report(message: SIPMessage):
    report = message.codec_support_report()
    return next(codec for codec in report["remote"] if codec["name"] == "PCMU")


def test_sdp_bandwidth_is_scoped_converted_and_reported():
    message = _invite_with_sdp(
        *_basic_sdp(
            "b=AS:80",
            "m=audio 4000 RTP/AVP 0 101",
            "b=TIAS:64000",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:101 telephone-event/8000",
            "a=fmtp:101 0-15",
        )
    )

    assert message.body["b"] == [
        {
            "type": "AS",
            "bandwidth": 80,
            "unit": "kbps",
            "bits_per_second": 80000,
            "scope": "session",
        }
    ]
    assert message.body["m"][0]["bandwidth"] == [
        {
            "type": "TIAS",
            "bandwidth": 64000,
            "unit": "bps",
            "bits_per_second": 64000,
            "scope": "media",
        }
    ]

    pcmu = _pcmu_from_report(message)
    assert pcmu["bandwidth"]["limit_bps"] == 64000
    assert pcmu["required_bandwidth_bps"] == 64000
    assert pcmu["bandwidth_supported"] is True
    assert pcmu["supported"] is True


def test_bandwidth_below_g711_marks_pcmu_unsupported():
    message = _invite_with_sdp(
        *_basic_sdp(
            "m=audio 4000 RTP/AVP 0",
            "b=TIAS:32000",
            "a=rtpmap:0 PCMU/8000",
        )
    )

    pcmu = _pcmu_from_report(message)
    assert pcmu["bandwidth"]["limit_bps"] == 32000
    assert pcmu["required_bandwidth_bps"] == 64000
    assert pcmu["bandwidth_supported"] is False
    assert pcmu["supported"] is False


def test_phone_rejects_audio_offer_when_bandwidth_cannot_carry_g711():
    message = _invite_with_sdp(
        *_basic_sdp(
            "m=audio 4000 RTP/AVP 0 101",
            "b=TIAS:32000",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:101 telephone-event/8000",
        )
    )
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
    )

    assert phone._has_compatible_audio_offer(message) is False


def test_phone_accepts_audio_offer_when_bandwidth_can_carry_g711():
    message = _invite_with_sdp(
        *_basic_sdp(
            "m=audio 4000 RTP/AVP 0 101",
            "b=TIAS:64000",
            "a=rtpmap:0 PCMU/8000",
            "a=rtpmap:101 telephone-event/8000",
        )
    )
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
    )

    assert phone._has_compatible_audio_offer(message) is True
