import pytest

from pyVoIP import RTP, SIP
from pyVoIP.VoIP import VoIPPhone


def sip_invite_with_sdp(sdp: str) -> SIP.SIPMessage:
    data = (
        "INVITE sip:alice@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK1\r\n"
        "From: <sip:bob@example.com>;tag=fromtag\r\n"
        "To: <sip:alice@example.com>\r\n"
        "Call-ID: test-call@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp)}\r\n"
        "\r\n"
        f"{sdp}"
    )
    return SIP.SIPMessage(data.encode("utf8"))


def test_select_transmittable_audio_codec_uses_negotiated_payload_number():
    payload_type, codec = RTP.select_transmittable_audio_codec(
        {
            101: RTP.PayloadType.EVENT,
            96: RTP.PayloadType.PCMU,
        }
    )

    assert payload_type == 96
    assert codec == RTP.PayloadType.PCMU


def test_select_transmittable_audio_codec_rejects_event_only():
    with pytest.raises(RTP.RTPParseError):
        RTP.select_transmittable_audio_codec({101: RTP.PayloadType.EVENT})


def test_select_transmittable_audio_codec_rejects_video_only():
    with pytest.raises(RTP.RTPParseError):
        RTP.select_transmittable_audio_codec({26: RTP.PayloadType.JPEG})


def test_rtp_client_keeps_dynamic_audio_payload_number_for_transmit():
    client = RTP.RTPClient(
        {96: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        RTP.TransmitType.SENDRECV,
    )

    assert client.preference == RTP.PayloadType.PCMU
    assert client.preference_payload_type == 96


def test_dynamic_rtpmap_audio_offer_is_compatible():
    sdp = (
        "v=0\r\n"
        "o=remote 1 1 IN IP4 192.0.2.20\r\n"
        "s=test\r\n"
        "c=IN IP4 192.0.2.20\r\n"
        "t=0 0\r\n"
        "m=audio 4000 RTP/AVP 96 101\r\n"
        "a=rtpmap:96 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
    )
    message = sip_invite_with_sdp(sdp)
    phone = VoIPPhone("sip.example.com", 5060, "alice", "secret", myIP="192.0.2.10")

    assert phone._has_compatible_audio_offer(message)


def test_default_audio_offer_includes_all_transmittable_audio_codecs_and_dtmf():
    phone = VoIPPhone("sip.example.com", 5060, "alice", "secret", myIP="192.0.2.10")

    assert phone._default_audio_offer() == {
        0: RTP.PayloadType.PCMU,
        8: RTP.PayloadType.PCMA,
        101: RTP.PayloadType.EVENT,
        112: RTP.PayloadType.PCMU_WB,
        113: RTP.PayloadType.PCMA_WB,
    }
