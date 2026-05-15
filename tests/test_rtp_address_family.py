import socket

import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import VoIPPhone


def test_rtp_client_selects_ipv4_socket_family():
    family = RTP.RTPClient._select_socket_family("192.0.2.10", "192.0.2.20")

    assert family == socket.AF_INET


def test_rtp_client_selects_ipv6_socket_family():
    family = RTP.RTPClient._select_socket_family(
        "2001:db8::10", "2001:db8::20"
    )

    assert family == socket.AF_INET6


def test_rtp_client_rejects_mixed_address_families():
    with pytest.raises(RTP.RTPParseError, match="different IP versions"):
        RTP.RTPClient._select_socket_family("192.0.2.10", "2001:db8::20")


def _invite_with_connection(address_type: str, address: str) -> SIP.SIPMessage:
    body = (
        "v=0\r\n"
        "o=caller 1 2 IN IP4 192.0.2.20\r\n"
        "s=test\r\n"
        f"c=IN {address_type} {address}\r\n"
        "t=0 0\r\n"
        "m=audio 4000 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
    )
    raw = (
        "INVITE sip:alice@sip.example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK-test\r\n"
        "From: <sip:bob@sip.example.com>;tag=fromtag\r\n"
        "To: <sip:alice@sip.example.com>\r\n"
        "Call-ID: address-family-test\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        + body
    )
    return SIP.SIPMessage(raw.encode("utf8"))


def test_phone_accepts_matching_rtp_address_family():
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
    )
    request = _invite_with_connection("IP4", "192.0.2.20")

    assert phone._has_compatible_rtp_address_family(request)


def test_phone_rejects_mixed_rtp_address_family():
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
    )
    request = _invite_with_connection("IP6", "2001:db8::20")

    assert not phone._has_compatible_rtp_address_family(request)
