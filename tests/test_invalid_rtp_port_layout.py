from rfcvoip import RTP
from rfcvoip.VoIP.VoIP import CallState, VoIPCall, VoIPPhone


class DummyRequest:
    def __init__(self, body):
        self.headers = {
            "Call-ID": "call-1",
            "From": {"raw": "<sip:alice@example.com>", "tag": "from-tag"},
            "To": {"raw": "<sip:bob@example.com>", "tag": ""},
        }
        self.body = body

    def summary(self):
        return "dummy request"


class DummyPhone:
    rtpPortLow = 10000
    rtpPortHigh = 10010
    sip = object()


class RecordingSIP:
    def __init__(self):
        self.sent = []

    def gen_ringing(self, request):
        return "180 Ringing"

    def gen_response(self, request, status):
        return f"{int(status)} {status.phrase}"

    def send_response(self, request, response):
        self.sent.append(response)


def _body_with_media(media):
    return {
        "c": [
            {
                "address": "198.51.100.10",
                "address_type": "IP4",
                "address_count": 2,
            }
        ],
        "m": media,
    }


def _audio_media(port_count):
    return {
        "type": "audio",
        "protocol": RTP.RTPProtocol.AVP,
        "port": 4000,
        "port_count": port_count,
        "methods": ["0"],
        "attributes": {"0": {}},
    }


def _video_media(port_count):
    return {
        "type": "video",
        "protocol": RTP.RTPProtocol.AVP,
        "port": 5000,
        "port_count": port_count,
        "methods": ["31"],
        "attributes": {"31": {}},
    }


def test_unassignable_rtp_ports_raise_parse_error():
    request = DummyRequest(
        _body_with_media(
            [
                _audio_media(port_count=1),
            ]
        )
    )

    try:
        VoIPCall(
            DummyPhone(),
            CallState.RINGING,
            request,
            1,
            "127.0.0.1",
        )
    except RTP.RTPParseError as ex:
        assert str(ex) == "Unable to assign ports for RTP."
    else:
        raise AssertionError("VoIPCall accepted an unassignable RTP layout")


def test_inbound_invite_sends_488_for_constructor_media_failure():
    callbacks = []
    phone = VoIPPhone(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        callCallback=callbacks.append,
    )
    phone.sip = RecordingSIP()
    request = DummyRequest(
        _body_with_media(
            [
                _audio_media(port_count=2),
                _video_media(port_count=1),
            ]
        )
    )

    phone._callback_MSG_Invite(request)

    assert phone.sip.sent == ["180 Ringing", "488 Not Acceptable Here"]
    assert callbacks == []
    assert request.headers["Call-ID"] not in phone.calls
    assert phone.assignedPorts == []
