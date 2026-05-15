from rfcvoip import RTP, SIP


class FakePhone:
    _status = None


def make_client() -> SIP.SIPClient:
    return SIP.SIPClient(
        "example.com",
        5060,
        "alice",
        "secret",
        phone=FakePhone(),
        myIP="127.0.0.1",
    )


def make_invite() -> SIP.SIPMessage:
    return SIP.SIPMessage(
        b"INVITE sip:alice@example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.2:5060;"
        b"branch=z9hG4bKabc;rport\r\n"
        b'From: "Bob" <sip:bob@example.com>;tag=fromtag\r\n'
        b"To: <sip:alice@example.com>\r\n"
        b"Call-ID: call-id-1\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Contact: <sip:bob@127.0.0.2:5060>\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )


def assert_empty_supported_header(response: str) -> None:
    assert "\r\nSupported:\r\n" in response
    assert "Supported: 100rel" not in response


def test_empty_supported_header_parses_as_no_option_tags() -> None:
    response = SIP.SIPMessage(
        b"SIP/2.0 200 OK\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.2:5060;"
        b"branch=z9hG4bKabc;rport\r\n"
        b"From: <sip:bob@example.com>;tag=fromtag\r\n"
        b"To: <sip:alice@example.com>;tag=totag\r\n"
        b"Call-ID: call-id-1\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Supported:\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )

    assert response.headers["Supported"] == []


def test_todo_response_generators_include_empty_supported_header() -> None:
    client = make_client()
    request = make_invite()
    call_id = request.headers["Call-ID"]
    client.tagLibrary[call_id] = "localtag"

    answer_media = {
        10000: {
            0: RTP.PayloadType.PCMU,
            101: RTP.PayloadType.EVENT,
        }
    }

    responses = [
        client.gen_sip_version_not_supported(request),
        client.gen_busy(request),
        client.gen_ringing(request),
        client.gen_answer(
            request,
            "1",
            answer_media,
            RTP.TransmitType.SENDRECV,
        ),
    ]

    for response in responses:
        assert_empty_supported_header(response)
