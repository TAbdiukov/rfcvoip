from rfcvoip import RTP
from rfcvoip.SIP import SIPMessage
from rfcvoip.VoIP import CallState, VoIPCall, VoIPPhone


def _invite_message(call_id: str = "test-call-id") -> SIPMessage:
    packet = (
        b"INVITE sip:answerme@127.0.0.1 SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKtest\r\n"
        b"From: <sip:alice@127.0.0.1>;tag=1234\r\n"
        b"To: <sip:answerme@127.0.0.1>\r\n"
        + f"Call-ID: {call_id}\r\n".encode("utf8")
        + b"CSeq: 1 INVITE\r\n"
        + b"Contact: <sip:alice@127.0.0.1:5060>\r\n"
        + b"Content-Length: 0\r\n\r\n"
    )
    return SIPMessage(packet)


def test_release_ports_is_idempotent_for_same_call():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "user",
        "pass",
        myIP="127.0.0.1",
    )
    call = VoIPCall(
        phone,
        CallState.DIALING,
        _invite_message(),
        1,
        "127.0.0.1",
        ms={10000: {0: RTP.PayloadType.PCMU}},
        sendmode="sendrecv",
    )
    phone.assignedPorts.append(10000)

    phone.release_ports(call=call)
    phone.release_ports(call=call)

    assert phone.assignedPorts == []


def test_voipcall_destructor_ignores_ports_already_released():
    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "user",
        "pass",
        myIP="127.0.0.1",
    )
    call = VoIPCall(
        phone,
        CallState.DIALING,
        _invite_message("test-call-id-2"),
        1,
        "127.0.0.1",
        ms={10001: {0: RTP.PayloadType.PCMU}},
        sendmode="sendrecv",
    )
    phone.assignedPorts.append(10001)

    phone.release_ports(call=call)
    call.__del__()

    assert phone.assignedPorts == []
