from types import SimpleNamespace

import pytest

import rfcvoip.Telemetry as Telemetry
from rfcvoip.RTP import PayloadType, TransmitType
from rfcvoip.SIP import SIPMessage


@pytest.fixture(autouse=True)
def reset_process_auth_telemetry():
    Telemetry._PROCESS_TELEMETRY["auth"] = {
        "last_digest": None,
        "digest_history": [],
    }
    yield
    Telemetry._PROCESS_TELEMETRY["auth"] = {
        "last_digest": None,
        "digest_history": [],
    }


def _parse_message(raw: str) -> SIPMessage:
    return SIPMessage(raw.encode("utf8"))


def _sip_response_with_sdp(sdp: str) -> SIPMessage:
    body = sdp.encode("utf8")
    return _parse_message(
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:100@example.test>;tag=fromtag\r\n"
        "To: <sip:200@example.test>;tag=totag\r\n"
        "Call-ID: call-1@example.test\r\n"
        "CSeq: 1 INVITE\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        f"{sdp}"
    )


def test_package_snapshot_reports_local_codec_capabilities_hilevel():
    snapshot = Telemetry.snapshot()

    assert snapshot["type"] == "package"
    assert snapshot["package"]["name"] == "rfcvoip"
    assert snapshot["auth"]["has_authenticated"] is False
    assert snapshot["codecs"]["local_can_start_call"] is True

    offered_codecs = {
        (codec["payload_type"], codec["name"])
        for codec in snapshot["codecs"]["local_offer"]
    }
    assert (0, "PCMU") in offered_codecs
    assert (8, "PCMA") in offered_codecs

def test_require_snapshot_reports_requirements_hilevel():
    snapshot = Telemetry.snapshot()
    for codec in snapshot["codecs"]["known_codecs"]:
        assert "extra_packages" in codec
        assert "package_extras" in codec
        assert "install_extras" in codec
        assert "extra_package" in codec
        assert "package_extra" in codec
        assert "install_extra" in codec

def test_sip_message_snapshot_reports_remote_sdp_compatibility_hilevel():
    message = _sip_response_with_sdp(
        "v=0\r\n"
        "o=- 1 1 IN IP4 127.0.0.1\r\n"
        "s=rfcvoip telemetry test\r\n"
        "c=IN IP4 203.0.113.10\r\n"
        "t=0 0\r\n"
        "b=AS:128\r\n"
        "m=audio 49170 RTP/AVP 0 8 101\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=fmtp:101 0-15\r\n"
        "a=sendrecv\r\n"
    )

    snapshot = Telemetry.sip_message_snapshot(message)
    codec_report = snapshot["codecs"]
    remote_by_payload = {
        codec["payload_type"]: codec for codec in codec_report["remote"]
    }

    assert snapshot["has_sdp"] is True
    assert codec_report["remote_has_sdp"] is True
    assert codec_report["can_start_call"] is True

    assert remote_by_payload[0]["name"] == "PCMU"
    assert remote_by_payload[0]["supported"] is True
    assert remote_by_payload[0]["bandwidth"]["limit_bps"] == 128000

    assert remote_by_payload[101]["name"] == "telephone-event"
    assert remote_by_payload[101]["can_transmit_audio"] is False


def test_auth_snapshot_reports_challenges_and_authorisation_hilevel():
    challenge = _parse_message(
        "SIP/2.0 401 Unauthorized\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:100@example.test>;tag=fromtag\r\n"
        "To: <sip:200@example.test>;tag=totag\r\n"
        "Call-ID: call-auth@example.test\r\n"
        "CSeq: 1 INVITE\r\n"
        'WWW-Authenticate: Digest realm="example.test",nonce="nonce",'
        'algorithm=SHA-256,qop="auth"\r\n'
        "Content-Length: 0\r\n"
        "\r\n"
    )

    challenge_auth = Telemetry.auth_snapshot(challenge)

    assert challenge_auth["has_authenticated"] is False
    assert challenge_auth["challenges"][0]["header"] == "WWW-Authenticate"
    assert challenge_auth["challenges"][0]["algorithm"] == "SHA-256"
    assert challenge_auth["challenges"][0]["nonce_present"] is True

    authorised_request = _parse_message(
        "INVITE sip:200@example.test SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:100@example.test>;tag=fromtag\r\n"
        "To: <sip:200@example.test>\r\n"
        "Call-ID: call-auth@example.test\r\n"
        "CSeq: 2 INVITE\r\n"
        'Authorization: Digest username="100",realm="example.test",'
        'nonce="nonce",uri="sip:200@example.test",response="abcdef",'
        'algorithm=SHA-256,qop=auth,nc=00000001,cnonce="cnonce"\r\n'
        "Content-Length: 0\r\n"
        "\r\n"
    )

    authorised_auth = Telemetry.auth_snapshot(authorised_request)
    last_digest = authorised_auth["last_digest"]

    assert authorised_auth["has_authenticated"] is True
    assert last_digest["algorithm"] == "SHA-256"
    assert last_digest["header"] == "Authorization"
    assert last_digest["uri_present"] is True
    assert "sip:" not in repr(authorised_auth).lower()


def test_record_digest_auth_scrubs_sip_urls_and_supports_get_hilevel():
    source = SimpleNamespace(_telemetry={})

    stored = Telemetry.record_digest_auth(
        source,
        {
            "algorithm": "SHA-512-256",
            "qop": "auth",
            "header": "Authorization",
            "request_uri": "sip:200@example.test",
            "target": "sip:200@example.test",
        },
    )

    auth = Telemetry.auth_snapshot(source)

    assert stored["algorithm"] == "SHA-512-256"
    assert "request_uri" not in stored
    assert "target" not in stored
    assert Telemetry.get(source, "auth.last_digest.algorithm") == "SHA-512-256"
    assert auth["has_authenticated"] is True
    assert "request_uri" not in auth["last_digest"]
    assert "sip:" not in repr(auth).lower()


def test_call_snapshot_and_reports_include_active_codec_hilevel():
    class DummyRTPClient:
        preference = PayloadType.PCMU
        preference_payload_type = 0
        inIP = "127.0.0.1"
        inPort = 10000
        outIP = "127.0.0.1"
        outPort = 20000
        sendrecv = TransmitType.SENDRECV
        codec_priority_scores = {}
        enabled_codecs = None

        def audio_format(self):
            return {
                "sample_rate": 8000,
                "channels": 1,
                "bit_depth": 8,
                "bits_per_sample": 8,
                "sample_width": 1,
                "sample_width_bytes": 1,
                "encoding": "linear_pcm",
                "sample_format": "u8",
                "signed": False,
                "endian": None,
                "frame_ms": 20,
                "frame_size": 160,
            }

    call = SimpleNamespace(
        call_id="call-1@example.test",
        session_id="1",
        state=SimpleNamespace(value="ANSWERED"),
        sendmode=TransmitType.SENDRECV,
        assignedPorts={10000: {0: PayloadType.PCMU}},
        RTPClients=[DummyRTPClient()],
        remote_sip_message=None,
        sip=None,
    )

    snapshot = Telemetry.snapshot(call)
    discord_report = Telemetry.discord_report(call)
    telegram_report = Telemetry.telegram_report(call)

    assert snapshot["type"] == "call"
    assert snapshot["selected_codec"]["name"] == "PCMU"
    assert snapshot["audio"]["bit_depth"] == 8
    assert snapshot["codecs"]["active_codecs"][0]["rtp"]["local"]["port"] == 10000

    assert "🎧 Codecs" in discord_report
    assert "• Active:" in discord_report
    assert "PCMU/8000:0" in discord_report
    assert "• Active:" in telegram_report