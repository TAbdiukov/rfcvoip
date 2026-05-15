from rfcvoip.SIP import SIPMessage


def test_folded_digest_header_is_unfolded():
    raw = (
        b"SIP/2.0 401 Unauthorized\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060\r\n"
        b"Call-ID: test-call\r\n"
        b"CSeq: 1 REGISTER\r\n"
        b'WWW-Authenticate: Digest realm="example",\r\n'
        b' nonce="abc123"\r\n'
        b"Content-Length: 0\r\n"
        b"\r\n"
    )

    message = SIPMessage(raw)

    assert message.authentication["realm"] == "example"
    assert message.authentication["nonce"] == "abc123"


def test_from_to_uri_params_do_not_pollute_host():
    raw = (
        b"INVITE sip:200@example.net SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        b'From: "Alice" <sip:100@example.com;user=phone>;tag=abc\r\n'
        b"To: <sip:200@example.net;user=phone>\r\n"
        b"Call-ID: test-call\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )

    message = SIPMessage(raw)

    assert message.headers["From"]["address"] == "100@example.com;user=phone"
    assert message.headers["From"]["number"] == "100"
    assert message.headers["From"]["host"] == "example.com"
    assert message.headers["To"]["number"] == "200"
    assert message.headers["To"]["host"] == "example.net"