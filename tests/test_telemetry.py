import pyVoIP
from pyVoIP import Telemetry
from pyVoIP.SIP import SIPMessage


class DummySIPClient:
    def __init__(self):
        self.telemetry = {"auth": {"last_digest": None, "history": []}}
        self.NSD = True
        self.server = "sip.example.com"
        self.server_host = "sip.example.com"
        self.server_port = 5060
        self.proxy = None
        self.proxy_port = None
        self.myIP = "127.0.0.1"
        self.myPort = 5060

    def signal_target(self):
        return ("sip.example.com", 5060)

    def signal_transport(self):
        return type("Transport", (), {"value": "UDP"})()

    def _build_digest_auth_header(self):
        raise NotImplementedError


def test_auth_snapshot_challenge_is_not_authenticated():
    msg = SIPMessage(
        b"SIP/2.0 401 Unauthorized\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK1\r\n"
        b"From: <sip:100@example.com>;tag=abc\r\n"
        b"To: <sip:100@example.com>\r\n"
        b"Call-ID: c1\r\n"
        b"CSeq: 1 REGISTER\r\n"
        b'WWW-Authenticate: Digest realm="r",nonce="n",qop="auth"\r\n'
        b"Content-Length: 0\r\n\r\n"
    )

    snap = Telemetry.auth_snapshot(msg)

    assert snap["has_authenticated"] is False
    assert snap["last_digest"] is None
    assert snap["challenges"]
    assert snap["challenges"][0]["header"] == "WWW-Authenticate"


def test_auth_snapshot_authorization_is_authenticated():
    msg = SIPMessage(
        b"REGISTER sip:example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK1\r\n"
        b"From: <sip:100@example.com>;tag=abc\r\n"
        b"To: <sip:100@example.com>\r\n"
        b"Call-ID: c1\r\n"
        b"CSeq: 2 REGISTER\r\n"
        b'Authorization: Digest username="u",realm="r",nonce="n",'
        b'uri="sip:example.com",response="deadbeef",algorithm=SHA-256,'
        b'qop=auth,nc=00000001,cnonce="c"\r\n'
        b"Content-Length: 0\r\n\r\n"
    )

    snap = Telemetry.auth_snapshot(msg)

    assert snap["has_authenticated"] is True
    assert snap["last_digest"]["header"] == "Authorization"
    assert snap["last_digest"]["algorithm"] == "SHA-256"
    assert snap["last_digest"]["qop"] == "auth"


def test_auth_snapshot_does_not_use_process_fallback_for_explicit_object():
    Telemetry.record_digest_auth({}, {"algorithm": "SHA-256"})

    snap = Telemetry.auth_snapshot(object())

    assert snap["has_authenticated"] is False
    assert snap["last_digest"] is None


def test_auth_snapshot_none_may_use_process_fallback():
    Telemetry.record_digest_auth({}, {"algorithm": "SHA-256"})

    snap = Telemetry.auth_snapshot(None)

    assert snap["has_authenticated"] is True
    assert snap["last_digest"]["algorithm"] == "SHA-256"
    assert snap["source"] == "process-fallback"


def test_record_digest_auth_updates_public_sip_client_telemetry():
    client = DummySIPClient()

    Telemetry.record_digest_auth(
        client,
        {
            "algorithm": "SHA-256",
            "qop": "auth",
            "header": "Authorization",
        },
    )

    assert client.telemetry["auth"]["last_digest"]["algorithm"] == "SHA-256"
    assert client.telemetry["auth"]["last_digest"]["qop"] == "auth"
    assert client.telemetry["auth"]["has_authenticated"] is True


def test_auth_snapshot_reads_public_sip_client_telemetry():
    client = DummySIPClient()
    client.telemetry["auth"]["last_digest"] = {
        "algorithm": "SHA-512-256",
        "qop": "auth",
    }

    snap = Telemetry.auth_snapshot(client)

    assert snap["has_authenticated"] is True
    assert snap["last_digest"]["algorithm"] == "SHA-512-256"


def test_codec_availability_single_codec_refresh(monkeypatch):
    calls = []

    def fake_refresh():
        calls.append(True)

    monkeypatch.setattr(
        "pyVoIP.codecs.refresh_codec_availability",
        fake_refresh,
    )

    result = Telemetry.codec_availability(
        pyVoIP.RTP.PayloadType.PCMU,
        refresh=True,
    )

    assert calls == [True]
    assert result["name"] == "PCMU"

