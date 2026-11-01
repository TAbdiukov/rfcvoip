import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState, PhoneStatus, VoIPCall, VoIPPhone
from rfcvoip.VoIP.VoIP import _payload_type_from_media_method


def invite(direction="sendrecv", *, count=1):
    body = (
        "v=0\r\n"
        "o=remote 1 1 IN IP4 127.0.0.1\r\n"
        "s=call\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        f"m=audio 40000 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        f"a={direction}\r\n"
    )
    if count == 2:
        body += "m=audio 40002 RTP/AVP 0\r\na=sendonly\r\n"
    packet = (
        "INVITE sip:100@127.0.0.1 SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        "From: <sip:200@127.0.0.1>;tag=remote\r\n"
        "To: <sip:100@127.0.0.1>\r\n"
        "Call-ID: lifecycle-call\r\n"
        "CSeq: 1 INVITE\r\n"
        "Contact: <sip:200@127.0.0.1:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body.encode('utf8'))}\r\n\r\n" + body
    )
    return SIP.SIPMessage(packet.encode("utf8"))


@pytest.fixture
def phone(monkeypatch):
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "100",
        "secret",
        myIP="127.0.0.1",
        rtpPortLow=30000,
        rtpPortHigh=30001,
        callCallback=lambda call: None,
    )
    phone.responses = []
    monkeypatch.setattr(
        phone.sip,
        "send_response",
        lambda request, message: phone.responses.append(message),
    )
    monkeypatch.setattr(RTP.RTPClient, "start", lambda self: None)
    return phone


def ringing_call(phone, direction="sendrecv", desired="sendrecv"):
    request = invite(direction)
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    call = VoIPCall(
        phone,
        CallState.RINGING,
        request,
        123,
        phone.myIP,
        sendmode=desired,
    )
    phone._set_call(call.call_id, call)
    phone.session_ids.append(123)
    return call


@pytest.mark.parametrize("initial_direction", ["sendrecv", "sendonly"])
def test_call_recovers_full_audio_direction_after_remote_hold(
    phone,
    initial_direction,
):
    call = ringing_call(phone, initial_direction)
    call.answer()

    call.renegotiate(invite("sendonly"))
    assert call.RTPClients[0].sendrecv is RTP.TransmitType.RECVONLY
    call.renegotiate(invite("sendrecv"))

    assert call.sendmode is RTP.TransmitType.SENDRECV
    assert call.RTPClients[0].sendrecv is RTP.TransmitType.SENDRECV
    assert "a=sendrecv\r\n" in phone.responses[-1]


def test_hold_resume_preserves_configured_direction(phone):
    call = ringing_call(phone, desired="recvonly")
    call.answer()

    call.renegotiate(invite("sendonly"))
    call.renegotiate(invite("sendrecv"))

    assert call.sendmode is RTP.TransmitType.RECVONLY
    assert call.RTPClients[0].sendrecv is RTP.TransmitType.RECVONLY


def test_rejected_renegotiation_preserves_direction(phone):
    call = ringing_call(phone)
    call.answer()

    call.renegotiate(invite("sendonly", count=2))

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert call.sendmode is RTP.TransmitType.SENDRECV
    assert call.RTPClients[0].sendrecv is RTP.TransmitType.SENDRECV


def test_failed_renegotiation_response_preserves_media(phone, monkeypatch):
    call = ringing_call(phone)
    call.answer()

    def fail(*args):
        raise OSError("connection lost")

    monkeypatch.setattr(phone.sip, "send_response", fail)
    with pytest.raises(OSError, match="connection lost"):
        call.renegotiate(invite("sendonly"))

    assert call.sendmode is RTP.TransmitType.SENDRECV
    assert call.RTPClients[0].sendrecv is RTP.TransmitType.SENDRECV


@pytest.mark.parametrize("method", ["cancel", "deny"])
def test_termination_releases_local_call_when_signaling_fails(
    phone,
    monkeypatch,
    method,
):
    call = ringing_call(phone)

    def fail(*args):
        raise OSError("connection lost")

    monkeypatch.setattr(phone.sip, "send_response", fail)
    monkeypatch.setattr(phone.sip, "cancel", fail)
    with pytest.raises(OSError, match="connection lost"):
        getattr(call, method)()

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []


def test_callback_thread_start_failure_releases_call(phone, monkeypatch):
    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr("rfcvoip.VoIP.VoIP.Thread", BrokenThread)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        phone._callback_MSG_Invite(invite())

    assert phone.responses[-1].startswith("SIP/2.0 486 ")
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []


def test_late_invite_failure_does_not_terminate_answered_call(
    phone,
    monkeypatch,
):
    call = ringing_call(phone)
    call.answer()
    acks = []
    monkeypatch.setattr(phone, "_send_ack", acks.append)
    response = SIP.SIPMessage(
        b"SIP/2.0 486 Busy Here\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKother\r\n"
        b"From: <sip:100@127.0.0.1>;tag=local\r\n"
        b"To: <sip:200@127.0.0.1>;tag=other\r\n"
        b"Call-ID: lifecycle-call\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Content-Length: 0\r\n\r\n"
    )

    phone.callback(response)

    assert acks == [response]
    assert call.state is CallState.ANSWERED
    assert phone._get_call(call.call_id) is call
    assert phone.assignedPorts


@pytest.mark.parametrize(
    "rtpmap",
    [
        {"name": "UNKNOWN-CODEC", "frequency": "8000"},
        {"name": "PCMU", "frequency": "16000"},
        {"name": "PCMU", "frequency": "8000", "encoding": "2"},
    ],
)
def test_explicit_unsupported_rtpmap_does_not_fall_back_to_static_codec(
    rtpmap,
):
    media = {"attributes": {"0": {"rtpmap": rtpmap}}}

    with pytest.raises(ValueError):
        _payload_type_from_media_method(media, "0")


def test_static_codec_still_works_without_rtpmap():
    assert _payload_type_from_media_method({}, "0") is RTP.PayloadType.PCMU


def test_double_start_preserves_running_phone(phone, monkeypatch):
    phone.NSD = phone.sip.NSD = True
    phone._status = PhoneStatus.REGISTERED
    stopped = []
    monkeypatch.setattr(phone.sip, "stop", lambda: stopped.append(True))

    with pytest.raises(RuntimeError, match="already started"):
        phone.start()

    assert stopped == []
    assert phone.NSD
    assert phone.get_status() is PhoneStatus.REGISTERED


def test_renegotiation_rejects_incompatible_payload_mapping(phone):
    call = ringing_call(phone)
    call.answer()
    request = invite()
    request.body["m"][0]["methods"] = ["8"]
    request.body["m"][0]["attributes"] = {}

    call.renegotiate(request)

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert call.state is CallState.ANSWERED
    assert call.RTPClients[0].assoc == {0: RTP.PayloadType.PCMU}


def test_renegotiation_rejects_invalid_port_count(phone):
    call = ringing_call(phone)
    call.answer()
    request = invite("sendonly")
    request.body["m"][0]["port_count"] = 2

    call.renegotiate(request)

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert call.sendmode is RTP.TransmitType.SENDRECV
