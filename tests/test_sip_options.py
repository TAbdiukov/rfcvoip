from rfcvoip import RTP
from rfcvoip.SIP import SIPClient, SIPMessage


class _Phone:
    codec_priorities = {}

    def _default_audio_offer(self):
        return {
            0: RTP.PayloadType.PCMU,
            101: RTP.PayloadType.EVENT,
        }


def _client() -> SIPClient:
    return SIPClient(
        "example.com",
        5060,
        "1000",
        "secret",
        _Phone(),
        myIP="192.0.2.10",
        myPort=5060,
    )


def _options_request(accept_header: str = "Accept: application/sdp\r\n") -> SIPMessage:
    return SIPMessage(
        (
            "OPTIONS sip:1000@example.com SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bKtest\r\n"
            "From: <sip:alice@example.com>;tag=alice\r\n"
            "To: <sip:1000@example.com>\r\n"
            "Call-ID: options-test\r\n"
            "CSeq: 1 OPTIONS\r\n"
            f"{accept_header}"
            "Content-Length: 0\r\n\r\n"
        ).encode("utf8")
    )


def test_options_ok_advertises_sdp_when_requested():
    response = _client().gen_options_ok(_options_request())
    headers, body = response.split("\r\n\r\n", 1)

    assert "Content-Type: application/sdp" in headers
    assert f"Content-Length: {len(body.encode('utf8'))}" in headers
    assert "m=audio 0 RTP/AVP 0 101" in body
    assert "a=rtpmap:0 PCMU/8000" in body
    assert "a=rtpmap:101 telephone-event/8000" in body
    assert "a=fmtp:101 0-15" in body


def test_options_ok_defaults_to_sdp_when_accept_is_absent():
    response = _client().gen_options_ok(_options_request(""))
    headers, body = response.split("\r\n\r\n", 1)

    assert "Content-Type: application/sdp" in headers
    assert "m=audio 0 RTP/AVP 0 101" in body


def test_options_ok_omits_sdp_when_not_accepted():
    response = _client().gen_options_ok(
        _options_request("Accept: text/plain\r\n")
    )
    headers, body = response.split("\r\n\r\n", 1)

    assert "Content-Type: application/sdp" not in headers
    assert "Content-Length: 0" in headers
    assert body == ""


def test_options_ok_omits_sdp_when_phone_has_no_codec_offer():
    class PhoneWithoutCodecOffer:
        pass

    client = SIPClient(
        "example.com",
        5060,
        "1000",
        "secret",
        PhoneWithoutCodecOffer(),
        myIP="192.0.2.10",
        myPort=5060,
    )

    response = client.gen_options_ok(_options_request())
    headers, body = response.split("\r\n\r\n", 1)

    assert "Content-Type: application/sdp" not in headers
    assert "Content-Length: 0" in headers
    assert body == ""