import pytest

from pyVoIP.SIP import SIPMessage, SIPParseError
from pyVoIP.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPFramingError,
    SIPTransport,
)


def _message(body: bytes = b"", content_type: str = "") -> bytes:
    headers = [
        b"INVITE sip:bob@example.com SIP/2.0",
        b"Via: SIP/2.0/UDP 127.0.0.1:5060",
        b'From: "Alice" <sip:alice@example.com>;tag=fromtag',
        b"To: <sip:bob@example.com>",
        b"Call-ID: hardening-test",
        b"CSeq: 1 INVITE",
    ]
    if content_type:
        headers.append(f"Content-Type: {content_type}".encode("utf8"))
    headers.append(f"Content-Length: {len(body)}".encode("utf8"))
    return b"\r\n".join(headers) + b"\r\n\r\n" + body


def test_duplicate_singleton_header_is_rejected():
    raw = _message().replace(
        b"To: <sip:bob@example.com>",
        b"From: <sip:mallory@example.com>;tag=other\r\n"
        b"To: <sip:bob@example.com>",
    )

    with pytest.raises(SIPParseError, match="Duplicate SIP header"):
        SIPMessage(raw)


def test_duplicate_list_header_is_combined_not_dropped():
    raw = _message().replace(
        b"Content-Length: 0",
        b"Allow: INVITE, ACK\r\nAllow: BYE\r\nContent-Length: 0",
    )

    message = SIPMessage(raw)

    assert message.headers["Allow"] == ["INVITE", "ACK", "BYE"]


def test_malformed_header_line_is_rejected():
    raw = _message().replace(
        b"To: <sip:bob@example.com>",
        b"Malformed Header Line\r\nTo: <sip:bob@example.com>",
    )

    with pytest.raises(SIPParseError, match="Malformed SIP header line"):
        SIPMessage(raw)


def test_malformed_sdp_line_is_rejected():
    body = b"v=0\r\nnot-an-sdp-line\r\n"

    with pytest.raises(SIPParseError, match="Malformed SDP body line"):
        SIPMessage(_message(body, "application/sdp"))


def test_multiple_sdp_parts_are_rejected():
    body = (
        b'--parts\r\nContent-Type: application/sdp\r\n\r\n'
        b"v=0\r\ns=one\r\n"
        b'--parts\r\nContent-Type: application/sdp\r\n\r\n'
        b"v=0\r\ns=two\r\n"
        b"--parts--\r\n"
    )

    with pytest.raises(SIPParseError, match="Multiple application/sdp"):
        SIPMessage(_message(body, 'multipart/mixed; boundary="parts"'))


def test_from_to_parser_ignores_display_name_sip_text():
    raw = _message().replace(
        b'From: "Alice" <sip:alice@example.com>;tag=fromtag',
        b'From: "display; sip:not-address" '
        b"<sip:alice@example.com;transport=udp>;tag=abc",
    )

    message = SIPMessage(raw)

    assert message.headers["From"]["tag"] == "abc"
    assert message.headers["From"]["number"] == "alice"
    assert message.headers["From"]["host"] == "example.com"
    assert message.headers["From"]["address"] == (
        "alice@example.com;transport=udp"
    )


def test_stream_framing_rejects_invalid_content_length():
    target = ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP)
    connection = SIPConnection("127.0.0.1", 0, target)
    connection._stream_buffer = (
        b"SIP/2.0 200 OK\r\nContent-Length: nope\r\n\r\n"
    )

    with pytest.raises(SIPFramingError, match="Content-Length"):
        connection._stream_message_length()


def test_stream_framing_rejects_conflicting_content_lengths():
    target = ResolvedSIPTarget("127.0.0.1", 5060, SIPTransport.TCP)
    connection = SIPConnection("127.0.0.1", 0, target)
    connection._stream_buffer = (
        b"SIP/2.0 200 OK\r\n"
        b"Content-Length: 0\r\n"
        b"Content-Length: 1\r\n\r\n"
    )

    with pytest.raises(SIPFramingError, match="Conflicting"):
        connection._stream_message_length()