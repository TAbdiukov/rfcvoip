"""Exercise output selection through real phone, call and native RTP objects."""
import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState, VoIPPhone


def sip_message(*, response=False, remote_port=40000, connections=1, cseq=1):
    connection = "127.0.0.1" if connections == 1 else f"224.2.1.1/127/{connections}"
    port = str(remote_port) if connections == 1 else f"{remote_port}/{connections}"
    body = (
        "v=0\r\no=remote 1 1 IN IP4 127.0.0.1\r\ns=call\r\n"
        f"c=IN IP4 {connection}\r\nt=0 0\r\n"
        f"m=audio {port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n"
    )
    start = "SIP/2.0 200 OK" if response else "INVITE sip:1001@127.0.0.1 SIP/2.0"
    raw = (
        f"{start}\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKoutput\r\n"
        "From: <sip:2002@127.0.0.1>;tag=remote\r\n"
        "To: <sip:1001@127.0.0.1>;tag=local\r\n"
        "Call-ID: output-mode@example.test\r\n"
        f"CSeq: {cseq} INVITE\r\n"
        "Contact: <sip:2002@127.0.0.1:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    )
    return SIP.SIPMessage(raw.encode("utf8"))


def make_phone(monkeypatch, mode):
    kwargs = {} if mode is None else {"audio_output_mode": mode}
    phone = VoIPPhone(
        "127.0.0.1", 5060, "1001", "secret", myIP="127.0.0.1",
        rtpPortLow=30000, rtpPortHigh=30005, **kwargs,
    )
    monkeypatch.setattr(phone.sip, "send_response", lambda *_args: None)
    return phone


@pytest.mark.parametrize("mode", [None, "buffered", "realtime"])
@pytest.mark.parametrize("connections", [1, 2])
def test_incoming_mode_is_configured_before_start_and_survives_renegotiation(
    monkeypatch, mode, connections,
):
    phone = make_phone(monkeypatch, mode)
    expected_mode = mode or "buffered"
    started_modes = []
    monkeypatch.setattr(
        RTP.RTPClient, "start", lambda client: started_modes.append(client.audio_output_mode)
    )
    request = sip_message(connections=connections)
    phone.sip.gen_ringing(request)
    phone._create_Call(request, 42)
    call = phone.calls[request.headers["Call-ID"]]
    clients = list(call.RTPClients)
    assert len(clients) == connections
    assert call.audio_output_mode == phone.audio_output_mode == expected_mode
    assert started_modes == []
    for client in clients:
        assert type(client) is RTP.RTPClient
        assert not client.NSD
        assert client.audio_output_mode == expected_mode
        client.pmout._clock = lambda: 0.0
        size = client.audio_frame_size()
        client.write(b"a" * size)
        assert client.pmout.read(size) == (
            b"\x80" * size if mode == "realtime" else b"a" * size
        )

    call.answer()
    assert call.state is CallState.ANSWERED
    assert started_modes == [expected_mode] * connections
    call.renegotiate(sip_message(remote_port=42000, connections=connections, cseq=2))
    assert call.RTPClients == clients
    assert [client.outPort for client in clients] == list(range(42000, 42000 + connections))
    assert started_modes == [expected_mode] * connections
    for client in clients:
        size = client.audio_frame_size()
        client.write(b"b" * size)
        assert client.pmout.read(size) == (b"a" if mode == "realtime" else b"b") * size


@pytest.mark.parametrize("mode", [None, "realtime"])
def test_outbound_answer_creates_native_rtp_with_the_phone_output_mode(monkeypatch, mode):
    phone = make_phone(monkeypatch, mode)
    request = sip_message()
    monkeypatch.setattr(
        phone.sip, "invite", lambda *_args: (request, request.headers["Call-ID"], 42)
    )
    started = []
    monkeypatch.setattr(RTP.RTPClient, "start", lambda client: started.append(client))
    call = phone.call("2002")
    assert call.state is CallState.DIALING
    assert call.RTPClients == []

    call.answered(sip_message(response=True, remote_port=42000))

    assert call.state is CallState.ANSWERED
    assert started == call.RTPClients
    assert len(started) == 1
    client = started[0]
    assert client.audio_output_mode == (mode or "buffered")
    size = client.audio_frame_size()
    client.write(b"a" * size * 20)
    assert client.pmout.available() == size * (7 if mode == "realtime" else 20)


@pytest.mark.parametrize("mode", [None, "", "live", "REALTIME", False])
def test_invalid_phone_output_mode_is_rejected_before_sip_creation(monkeypatch, mode):
    def unexpected_sip_client(*_args, **_kwargs):
        raise AssertionError("SIP must not be constructed for an invalid output mode")

    monkeypatch.setattr(SIP, "SIPClient", unexpected_sip_client)
    with pytest.raises(ValueError, match="audio_output_mode"):
        VoIPPhone("127.0.0.1", 5060, "1001", "secret", audio_output_mode=mode)


@pytest.mark.parametrize("mode", [None, "", "live", "REALTIME", False])
def test_invalid_rtp_output_mode_is_rejected_during_construction(mode):
    with pytest.raises(RTP.RTPParseError, match="audio_output_mode"):
        RTP.RTPClient(
            {0: RTP.PayloadType.PCMU}, "127.0.0.1", 40000,
            "127.0.0.1", 40002, RTP.TransmitType.SENDRECV,
            audio_output_mode=mode,
        )
