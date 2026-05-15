from rfcvoip import RTP, SIP
from rfcvoip.VoIP import VoIPPhone


def _invite(*protocols: str) -> SIP.SIPMessage:
    media = []
    for index, protocol in enumerate(protocols):
        media.extend(
            [
                f"m=audio {4000 + (index * 2)} {protocol} 0",
                "a=rtpmap:0 PCMU/8000",
            ]
        )

    body = "\r\n".join(
        [
            "v=0",
            "o=alice 1 1 IN IP4 198.51.100.20",
            "s=test",
            "c=IN IP4 198.51.100.20",
            "t=0 0",
            *media,
            "a=sendrecv",
            "",
        ]
    )
    message = (
        "INVITE sip:1000@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 198.51.100.20:5060;branch=z9hG4bK-test\r\n"
        "From: <sip:alice@example.com>;tag=abc\r\n"
        "To: <sip:1000@example.com>\r\n"
        "Call-ID: avp-test@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:alice@198.51.100.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        f"{body}"
    )
    return SIP.SIPMessage(message.encode("utf8"))


def _phone() -> VoIPPhone:
    return VoIPPhone(
        "sip.example.com",
        5060,
        "1000",
        "secret",
        myIP="192.0.2.10",
        rtpPortLow=10000,
        rtpPortHigh=10010,
    )


def test_savp_audio_offer_is_not_compatible_without_srtp_support():
    request = _invite("RTP/SAVP")
    assert request.body["m"][0]["protocol"] == RTP.RTPProtocol.SAVP

    assert not _phone()._has_compatible_audio_offer(request)


def test_plain_avp_audio_offer_is_compatible():
    request = _invite("RTP/AVP")
    assert request.body["m"][0]["protocol"] == RTP.RTPProtocol.AVP

    assert _phone()._has_compatible_audio_offer(request)


def test_mixed_offer_only_creates_rtp_clients_for_plain_avp_media():
    request = _invite("RTP/SAVP", "RTP/AVP")
    phone = _phone()

    phone._create_Call(request, 123)

    call = phone.calls["avp-test@example.com"]
    assert len(call.RTPClients) == 1
    assert call.RTPClients[0].outPort == 4002
