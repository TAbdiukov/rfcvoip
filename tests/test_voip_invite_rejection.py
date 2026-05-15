from rfcvoip import SIP
from rfcvoip.VoIP.VoIP import VoIPPhone


def _invite_with_sdp(body: str) -> SIP.SIPMessage:
    raw = (
        "INVITE sip:alice@192.0.2.10 SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK-test\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:bob@example.com>;tag=from-tag\r\n"
        "To: <sip:alice@example.com>\r\n"
        "Call-ID: invalid-rtp-ports@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body.encode('utf8'))}\r\n"
        "\r\n"
        f"{body}"
    )
    return SIP.SIPMessage(raw.encode("utf8"))


def test_unassignable_rtp_ports_rejects_invite_without_ringing():
    sent_responses = []
    app_callbacks = []
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
        callCallback=app_callbacks.append,
    )
    phone.sip.send_response = (
        lambda request, response: sent_responses.append(response)
    )

    request = _invite_with_sdp(
        "v=0\r\n"
        "o=bob 1 1 IN IP4 192.0.2.20\r\n"
        "s=test\r\n"
        "c=IN IP4 192.0.2.20\r\n"
        "t=0 0\r\n"
        "m=audio 40000/2 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendrecv\r\n"
    )

    phone._callback_MSG_Invite(request)

    assert len(sent_responses) == 1
    assert sent_responses[0].startswith(
        "SIP/2.0 488 Not Acceptable Here\r\n"
    )
    assert request.headers["Call-ID"] not in phone.calls
    assert phone.assignedPorts == []
    assert app_callbacks == []
