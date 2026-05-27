import pytest

from rfcvoip import RTP
from rfcvoip.SIP import (
    SIPClient,
    SIPMessage,
    SIPMessageType,
    SIPParseError,
    SIPStatus,
    codec_bandwidth_supported,
    extract_sdp_bodies,
)


class _FakePhone:
    def _default_audio_offer(self):
        return {
            0: RTP.PayloadType.PCMU,
            101: RTP.PayloadType.EVENT,
        }


def _client():
    return SIPClient(
        "pbx.example",
        5060,
        "alice",
        "s3cret",
        phone=_FakePhone(),
        myIP="192.0.2.10",
        myPort=5060,
    )


def _message_bytes(start_and_headers: str, body: bytes = b"") -> bytes:
    return (
        start_and_headers + f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("utf8") + body


def test_sip_message_parses_invite_sdp_media_and_headers():
    sdp = (
        "v=0\r\n"
        "o=remote 1 1 IN IP4 192.0.2.20\r\n"
        "s=call\r\n"
        "c=IN IP4 192.0.2.20\r\n"
        "b=AS:64\r\n"
        "t=0 0\r\n"
        "m=audio 4000 RTP/AVP 0 101\r\n"
        "b=TIAS:64000\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
        "a=sendrecv\r\n"
    ).encode("utf8")
    raw = _message_bytes(
        "INVITE sip:alice@example.com SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK1;rport\r\n"
        "From: <sip:bob@example.net>;tag=from1\r\n"
        "To: <sip:alice@example.com>\r\n"
        "Call-ID: call-1\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        "Content-Type: application/sdp\r\n",
        sdp,
    )

    message = SIPMessage(raw)

    assert message.type == SIPMessageType.MESSAGE
    assert message.method == "INVITE"
    assert message.headers["From"]["number"] == "bob"
    assert message.headers["From"]["tag"] == "from1"
    assert message.headers["Via"][0]["rport"] is None
    assert message.headers["Content-Length"] == len(sdp)

    assert message.body["v"] == 0
    assert message.body["session_connections"][0]["address"] == "192.0.2.20"
    assert message.body["b"][0]["type"] == "AS"
    assert message.body["b"][0]["bits_per_second"] == 64000

    media = message.body["m"][0]
    assert media["type"] == "audio"
    assert media["port"] == 4000
    assert media["protocol"] == RTP.RTPProtocol.AVP
    assert media["methods"] == ["0", "101"]
    assert media["bandwidth"][0]["type"] == "TIAS"
    assert media["bandwidth"][0]["bits_per_second"] == 64000
    assert media["transmit_type"] == RTP.TransmitType.SENDRECV
    assert media["attributes"]["0"]["rtpmap"]["name"] == "PCMU"
    assert media["attributes"]["101"]["rtpmap"]["name"] == "telephone-event"
    assert media["attributes"]["101"]["fmtp"]["settings"] == ["0-15"]


def test_sip_message_extracts_sdp_from_multipart_body():
    boundary = "outer-boundary"
    sdp = (
        "v=0\r\n"
        "o=remote 2 2 IN IP4 192.0.2.30\r\n"
        "s=call\r\n"
        "c=IN IP4 192.0.2.30\r\n"
        "t=0 0\r\n"
        "m=audio 5004 RTP/AVP 96\r\n"
        "a=rtpmap:96 opus/48000/2\r\n"
    )
    multipart = (
        f"--{boundary}\r\n"
        "Content-Type: text/plain\r\n"
        "\r\n"
        "ignored\r\n"
        f"--{boundary}\r\n"
        "Content-Type: application/sdp\r\n"
        "\r\n"
        f"{sdp}"
        f"--{boundary}--\r\n"
    ).encode("utf8")
    raw = _message_bytes(
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bK2\r\n"
        "From: <sip:alice@example.com>;tag=local\r\n"
        "To: <sip:bob@example.net>;tag=remote\r\n"
        "Call-ID: call-2\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Content-Type: multipart/mixed; boundary={boundary}\r\n",
        multipart,
    )

    sdp_bodies = extract_sdp_bodies(
        f"multipart/mixed; boundary={boundary}",
        multipart,
    )
    message = SIPMessage(raw)

    assert sdp_bodies
    assert sdp_bodies[0].startswith(b"v=0")
    assert message.type == SIPMessageType.RESPONSE
    assert message.status == SIPStatus.OK
    assert message.body_raw == multipart
    assert message.body["m"][0]["port"] == 5004
    assert message.body["m"][0]["attributes"]["96"]["rtpmap"] == {
        "id": "96",
        "name": "opus",
        "frequency": "48000",
        "encoding": "2",
    }


def test_sip_message_rejects_conflicting_content_lengths():
    raw = (
        b"OPTIONS sip:alice@example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK3\r\n"
        b"From: <sip:bob@example.net>;tag=bob\r\n"
        b"To: <sip:alice@example.com>\r\n"
        b"Call-ID: call-3\r\n"
        b"CSeq: 1 OPTIONS\r\n"
        b"Content-Length: 0\r\n"
        b"l: 1\r\n"
        b"\r\n"
        b"x"
    )

    with pytest.raises(SIPParseError, match="Conflicting Content-Length"):
        SIPMessage(raw)


def test_gen_register_uses_best_digest_challenge_without_leaking_password():
    challenge = SIPMessage(
        _message_bytes(
            "SIP/2.0 401 Unauthorized\r\n"
            "Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKreg\r\n"
            'From: "alice" <sip:alice@pbx.example>;tag=local\r\n'
            'To: "alice" <sip:alice@pbx.example>;tag=remote\r\n'
            "Call-ID: reg-1\r\n"
            "CSeq: 1 REGISTER\r\n"
            'WWW-Authenticate: Digest realm="pbx",'
            'nonce="md5nonce",algorithm=MD5,qop="auth"\r\n'
            'WWW-Authenticate: Digest realm="pbx",'
            'nonce="sha256nonce",algorithm=SHA-256,qop="auth"\r\n',
        )
    )

    request = _client().gen_register(challenge)

    assert "Authorization: Digest " in request
    assert "Proxy-Authorization" not in request
    assert 'username="alice"' in request
    assert 'nonce="sha256nonce"' in request
    assert "algorithm=SHA-256" in request
    assert "s3cret" not in request


def test_gen_options_ok_returns_local_sdp_when_sdp_is_accepted():
    request = SIPMessage(
        _message_bytes(
            "OPTIONS sip:alice@pbx.example SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bKopt;rport\r\n"
            "From: <sip:bob@example.net>;tag=bob\r\n"
            "To: <sip:alice@pbx.example>\r\n"
            "Call-ID: opt-1\r\n"
            "CSeq: 42 OPTIONS\r\n"
            "Accept: application/sdp\r\n",
        )
    )

    response_text = _client().gen_options_ok(request)
    response = SIPMessage(response_text.encode("utf8"))

    assert response.status == SIPStatus.OK
    assert response.headers["Content-Type"] == "application/sdp"

    media = response.body["m"][0]
    assert media["type"] == "audio"
    assert media["port"] == 0
    assert media["methods"] == ["0", "101"]
    assert media["attributes"]["0"]["rtpmap"]["name"] == "PCMU"
    assert media["attributes"]["101"]["rtpmap"]["name"] == "telephone-event"
    assert media["attributes"]["101"]["fmtp"]["settings"] == ["0-15"]


def test_gen_invite_uses_normalized_target_and_structured_media_spec():
    client = _client()
    invite = client.gen_invite(
        "1001",
        "7",
        {
            20000: {
                "media_type": "audio",
                "port_count": 2,
                "codecs": {
                    0: RTP.PayloadType.PCMU,
                    101: RTP.PayloadType.EVENT,
                },
            }
        },
        RTP.TransmitType.SENDRECV,
        "z9hG4bKinvite",
        "invite-call-1",
    )

    message = SIPMessage(invite.encode("utf8"))

    assert message.method == "INVITE"
    assert message.uri == "sip:1001@pbx.example"
    assert message.headers["To"]["number"] == "1001"
    assert message.headers["Content-Length"] == len(message.body_raw)

    media = message.body["m"][0]
    assert media["type"] == "audio"
    assert media["port"] == 20000
    assert media["port_count"] == 2
    assert media["methods"] == ["0", "101"]
    assert media["transmit_type"] == RTP.TransmitType.SENDRECV


def test_codec_bandwidth_supported_applies_only_codec_media_limits():
    adequate_limit = [
        {
            "type": "TIAS",
            "bandwidth": 64000,
            "unit": "bps",
            "bits_per_second": 64000,
        }
    ]
    too_low_limit = [
        {
            "type": "AS",
            "bandwidth": 63,
            "unit": "kbps",
            "bits_per_second": 63000,
        }
    ]
    conference_total_limit = [
        {
            "type": "CT",
            "bandwidth": 1,
            "unit": "kbps",
            "bits_per_second": 1000,
        }
    ]

    assert codec_bandwidth_supported(
        RTP.PayloadType.PCMU,
        media_bandwidth=adequate_limit,
    )
    assert not codec_bandwidth_supported(
        RTP.PayloadType.PCMU,
        media_bandwidth=too_low_limit,
    )
    assert codec_bandwidth_supported(
        RTP.PayloadType.PCMU,
        session_bandwidth=conference_total_limit,
    )