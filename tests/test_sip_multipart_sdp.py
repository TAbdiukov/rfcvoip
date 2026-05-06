import pytest

from pyVoIP.SIP import SIPMessage, SIPMessageType, SIPStatus


SDP = (
    "v=0\r\n"
    "o=- 1 1 IN IP4 192.0.2.20\r\n"
    "s=pyVoIP test\r\n"
    "c=IN IP4 192.0.2.20\r\n"
    "t=0 0\r\n"
    "m=audio 4000 RTP/AVP 0 8 101\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:8 PCMA/8000\r\n"
    "a=rtpmap:101 telephone-event/8000\r\n"
    "a=fmtp:101 0-15\r\n"
    "a=sendrecv\r\n"
)


def _request(content_type: str, body: str, *, content_length=None) -> bytes:
    body_bytes = body.encode("utf-8")
    if content_length is None:
        content_length = len(body_bytes)

    headers = (
        "INVITE sip:bob@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:alice@example.com>;tag=alice-tag\r\n"
        "To: <sip:bob@example.com>\r\n"
        "Call-ID: test-call@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:alice@192.0.2.10:5060>\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {content_length}\r\n"
        "\r\n"
    ).encode("utf-8")
    return headers + body_bytes


def _response(content_type: str, body: str, *, content_length=None) -> bytes:
    body_bytes = body.encode("utf-8")
    if content_length is None:
        content_length = len(body_bytes)

    headers = (
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:alice@example.com>;tag=alice-tag\r\n"
        "To: <sip:bob@example.com>;tag=bob-tag\r\n"
        "Call-ID: test-call@example.com\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {content_length}\r\n"
        "\r\n"
    ).encode("utf-8")
    return headers + body_bytes


def _multipart(boundary: str, parts) -> str:
    body = []
    for content_type, payload in parts:
        body.extend(
            [
                f"--{boundary}\r\n",
                f"Content-Type: {content_type}\r\n",
                "\r\n",
                payload,
                "\r\n",
            ]
        )
    body.append(f"--{boundary}--\r\n")
    return "".join(body)


def _assert_sdp_parsed(message: SIPMessage) -> None:
    assert message.body["v"] == 0
    assert message.body["c"][0]["address"] == "192.0.2.20"

    media = message.body["m"][0]
    assert media["type"] == "audio"
    assert media["port"] == 4000
    assert media["methods"] == ["0", "8", "101"]

    assert media["attributes"]["0"]["rtpmap"]["name"] == "PCMU"
    assert media["attributes"]["8"]["rtpmap"]["name"] == "PCMA"
    assert media["attributes"]["101"]["rtpmap"]["name"] == "telephone-event"
    assert media["attributes"]["101"]["fmtp"]["settings"] == ["0-15"]


@pytest.mark.parametrize(
    "content_type",
    [
        "application/sdp",
        "application/sdp; charset=utf-8",
    ],
)
def test_application_sdp_body_is_parsed(content_type):
    message = SIPMessage(_request(content_type, SDP))

    assert message.type == SIPMessageType.MESSAGE
    assert message.method == "INVITE"
    _assert_sdp_parsed(message)


def test_multipart_mixed_sdp_part_is_extracted_from_request():
    boundary = "pyvoip-mixed-boundary"
    body = _multipart(
        boundary,
        [
            ("text/plain", "human readable call description"),
            ("application/sdp; charset=utf-8", SDP),
        ],
    )

    message = SIPMessage(
        _request(f"multipart/mixed; boundary={boundary}", body)
    )

    assert message.type == SIPMessageType.MESSAGE
    assert message.method == "INVITE"
    _assert_sdp_parsed(message)


def test_multipart_alternative_sdp_part_is_extracted():
    boundary = "pyvoip-alternative-boundary"
    body = _multipart(
        boundary,
        [
            ("text/plain", "call offer"),
            ("application/sdp", SDP),
        ],
    )

    message = SIPMessage(
        _request(f"multipart/alternative; boundary={boundary}", body)
    )

    _assert_sdp_parsed(message)


def test_multipart_sdp_part_is_extracted_from_response():
    boundary = "pyvoip-response-boundary"
    body = _multipart(
        boundary,
        [
            ("text/plain", "answer accepted"),
            ("application/sdp", SDP),
        ],
    )

    message = SIPMessage(
        _response(f"multipart/mixed; boundary={boundary}", body)
    )

    assert message.type == SIPMessageType.RESPONSE
    assert message.status == SIPStatus.OK
    _assert_sdp_parsed(message)


def test_nested_multipart_sdp_part_is_extracted():
    inner_boundary = "pyvoip-inner-boundary"
    outer_boundary = "pyvoip-outer-boundary"

    inner = _multipart(
        inner_boundary,
        [
            ("text/plain", "nested alternative"),
            ("application/sdp", SDP),
        ],
    )

    outer = _multipart(
        outer_boundary,
        [
            ("text/plain", "outer text"),
            (
                f"multipart/alternative; boundary={inner_boundary}",
                inner,
            ),
        ],
    )

    message = SIPMessage(
        _request(f"multipart/mixed; boundary={outer_boundary}", outer)
    )

    _assert_sdp_parsed(message)


def test_multipart_with_no_sdp_part_does_not_parse_structured_sdp():
    boundary = "pyvoip-no-sdp-boundary"
    body = _multipart(
        boundary,
        [
            # Deliberately SDP-looking text. It must not be parsed because the
            # MIME part is not application/sdp.
            ("text/plain", SDP),
        ],
    )

    message = SIPMessage(
        _request(f"multipart/mixed; boundary={boundary}", body)
    )

    assert message.body == {}
    assert message.body_raw == body.encode("utf-8")


def test_content_length_zero_does_not_parse_body():
    message = SIPMessage(
        _request("application/sdp", SDP, content_length=0)
    )

    assert message.body == {}
    assert message.body_raw == b""
    assert message.body_text == ""