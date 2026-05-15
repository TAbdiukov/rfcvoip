from types import SimpleNamespace

import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP.VoIP import CallState, VoIPCall, VoIPPhone


class DummyPhone:
    rtpPortLow = 10000
    rtpPortHigh = 20000

    def __init__(self):
        self.sip = object()
        self.calls = {}
        self.assignedPorts = []

    def request_port(self):
        port = 12000
        self.assignedPorts.append(port)
        return port


def _request_with_audio_media(media):
    return SimpleNamespace(
        headers={"Call-ID": "call-1"},
        body={
            "c": [
                {
                    "address_count": 1,
                    "address": "192.0.2.20",
                }
            ],
            "m": media,
        },
    )


def test_ringing_call_rejects_offer_with_no_transmittable_audio_codec():
    request = _request_with_audio_media(
        [
            {
                "type": "audio",
                "port": 4000,
                "port_count": 1,
                "methods": ["101"],
                "attributes": {
                    "101": {
                        "rtpmap": {
                            "name": "telephone-event",
                        }
                    }
                },
            }
        ]
    )

    with pytest.raises(RTP.RTPParseError):
        VoIPCall(DummyPhone(), CallState.RINGING, request, 1, "192.0.2.10")


def test_ringing_call_skips_unsupported_audio_media_when_supported_media_exists():
    request = _request_with_audio_media(
        [
            {
                "type": "audio",
                "port": 4000,
                "port_count": 1,
                "methods": ["18", "101"],
                "attributes": {
                    "18": {"rtpmap": {"name": "G729"}},
                    "101": {"rtpmap": {"name": "telephone-event"}},
                },
            },
            {
                "type": "audio",
                "port": 4002,
                "port_count": 1,
                "methods": ["0", "101"],
                "attributes": {
                    "0": {"rtpmap": {"name": "PCMU"}},
                    "101": {"rtpmap": {"name": "telephone-event"}},
                },
            },
        ]
    )

    call = VoIPCall(DummyPhone(), CallState.RINGING, request, 1, "192.0.2.10")

    assert len(call.RTPClients) == 1
    assert call.RTPClients[0].assoc == {
        0: RTP.PayloadType.PCMU,
        101: RTP.PayloadType.EVENT,
    }


def _invite_with_sdp(sdp):
    body = sdp.replace("\n", "\r\n")
    raw = (
        "INVITE sip:alice@192.0.2.10 SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.0.2.20:5060;branch=z9hG4bK-test\r\n"
        "From: <sip:bob@example.com>;tag=abc\r\n"
        "To: <sip:alice@example.com>\r\n"
        "Call-ID: call-2\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:bob@192.0.2.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
        f"{body}"
    )
    return SIP.SIPMessage(raw.encode("utf8"))


def test_phone_replies_488_to_invite_with_no_supported_audio_codec():
    request = _invite_with_sdp(
        "v=0\n"
        "o=bob 1 2 IN IP4 192.0.2.20\n"
        "s=test\n"
        "c=IN IP4 192.0.2.20\n"
        "t=0 0\n"
        "m=audio 4000 RTP/AVP 18 101\n"
        "a=rtpmap:18 G729/8000\n"
        "a=rtpmap:101 telephone-event/8000\n"
        "a=fmtp:101 0-15\n"
    )
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="192.0.2.10",
        callCallback=lambda call: None,
    )
    sent = []
    phone.sip.send_response = lambda req, message: sent.append(message)

    phone._callback_MSG_Invite(request)

    assert sent[0].startswith("SIP/2.0 488 Not Acceptable Here\r\n")
    assert phone.calls == {}
