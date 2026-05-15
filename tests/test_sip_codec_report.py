from rfcvoip.SIP import SIPMessage


def _sip_response_with_sdp(sdp: bytes) -> bytes:
    headers = (
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:alice@example.com>;tag=alice\r\n"
        "To: <sip:bob@example.com>;tag=bob\r\n"
        "Call-ID: codec-report-test\r\n"
        "CSeq: 1 INVITE\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp)}\r\n"
        "\r\n"
    )
    return headers.encode("utf-8") + sdp


def test_sip_message_reports_supported_sdp_codecs():
    from rfcvoip import Telemetry

    sdp = (
        "v=0\r\n"
        "o=rfcvoip 1 1 IN IP4 127.0.0.1\r\n"
        "s=rfcvoip test\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        "m=audio 4000 RTP/AVP 0 8 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
        "a=sendrecv\r\n"
    ).encode("utf-8")

    message = SIPMessage(_sip_response_with_sdp(sdp))
    codecs = Telemetry.sip_supported_codecs(message)
    names_by_payload = {
        codec["payload_type"]: codec["name"] for codec in codecs
    }

    assert names_by_payload[0] == "PCMU"
    assert names_by_payload[8] == "PCMA"
    assert names_by_payload[101] == "telephone-event"

    report = Telemetry.codec_support_report(message)
    assert report["remote_has_sdp"] is True
    assert report["can_start_call"] is True
    assert any(
        codec["supported"] and codec["can_transmit_audio"]
        for codec in report["compatible"]
    )