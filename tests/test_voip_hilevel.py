import pytest

import rfcvoip.VoIP.VoIP as VoIPModule
from rfcvoip import RTP, SIP


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, name=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.name = name
        self.daemon = daemon
        self._alive = False

    def start(self):
        self._alive = True
        try:
            self._target(*self._args, **self._kwargs)
        finally:
            self._alive = False

    def is_alive(self):
        return self._alive


class FakeRTPClient:
    instances = []

    def __init__(
        self,
        assoc,
        inIP,
        inPort,
        outIP,
        outPort,
        sendrecv,
        dtmf=None,
        **kwargs,
    ):
        self.assoc = dict(assoc)
        self.inIP = inIP
        self.inPort = inPort
        self.outIP = outIP
        self.outPort = outPort
        self.sendrecv = sendrecv
        self.dtmf = dtmf
        self.started = False
        self.stopped = False
        self.writes = []
        self.dtmf_sequences = []
        self.codec_priority_scores = dict(kwargs.get("codec_priority_scores") or {})
        self.enabled_codecs = kwargs.get("enabled_codecs")

        self.preference_payload_type, self.preference = (
            RTP.select_transmittable_audio_codec(
                self.assoc,
                priority_scores=self.codec_priority_scores,
                enabled_codecs=self.enabled_codecs,
            )
        )

        self.audio_sample_rate = int(kwargs.get("audio_sample_rate") or 8000)
        self.audio_channels = int(kwargs.get("audio_channels") or 1)
        bit_depth = kwargs.get("audio_bit_depth", 8)
        if bit_depth == "best":
            bit_depth = 8
        self.audio_bit_depth = int(bit_depth)
        self.audio_sample_width = max(1, self.audio_bit_depth // 8)
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def write(self, data):
        self.writes.append(data)

    def read(self, length, blocking=True):
        silence = b"\x80" if self.audio_bit_depth == 8 else b"\x00"
        return silence * length

    def send_dtmf_sequence(self, digits, *, allow_abcd=True):
        self.dtmf_sequences.append(digits)
        return True

    def audio_frame_size(self, duration_ms=None):
        duration_ms = 20 if duration_ms is None else int(duration_ms)
        return max(
            1,
            int(
                round(
                    self.audio_sample_rate
                    * self.audio_sample_width
                    * self.audio_channels
                    * (duration_ms / 1000.0)
                )
            ),
        )

    def public_audio_format(self, duration_ms=20):
        return VoIPModule.PublicAudioFormat(
            sample_rate=self.audio_sample_rate,
            channels=self.audio_channels,
            bit_depth=self.audio_bit_depth,
            frame_ms=duration_ms,
        )

    def audio_format(self, duration_ms=20):
        return self.public_audio_format(duration_ms).as_dict()


@pytest.fixture(autouse=True)
def isolate_network_edges(monkeypatch):
    FakeRTPClient.instances = []
    monkeypatch.setattr(VoIPModule.RTP, "RTPClient", FakeRTPClient)
    monkeypatch.setattr(VoIPModule, "Thread", ImmediateThread)
    monkeypatch.setattr(
        VoIPModule.VoIPPhone,
        "_run_call_callback",
        staticmethod(lambda callback, call, delay=1.0: callback(call)),
    )


def make_phone(monkeypatch, *, rtp_port=30000, callback=None):
    responses = []
    raw_messages = []

    phone = VoIPModule.VoIPPhone(
        "127.0.0.1",
        5060,
        "1001",
        "secret",
        myIP="127.0.0.1",
        sipPort=5062,
        rtpPortLow=rtp_port,
        rtpPortHigh=rtp_port,
        callCallback=callback or (lambda call: None),
    )

    monkeypatch.setattr(
        phone.sip,
        "send_response",
        lambda request, response: responses.append(response),
    )

    def send_raw(data, target=None):
        if isinstance(data, bytes):
            data = data.decode("utf8")
        raw_messages.append((data, target))

    monkeypatch.setattr(phone.sip, "send_raw", send_raw)
    return phone, responses, raw_messages


def sip_message(start_line, headers, body=""):
    headers = list(headers)
    if body:
        headers.append("Content-Type: application/sdp")
    headers.append(f"Content-Length: {len(body.encode('utf8'))}")

    raw = start_line + "\r\n" + "\r\n".join(headers) + "\r\n\r\n" + body
    return SIP.SIPMessage(raw.encode("utf8"))


def sdp_with_pcmu(*, port=40000, include_dtmf=True):
    methods = "0 101" if include_dtmf else "0"
    lines = [
        "v=0",
        "o=caller 1 1 IN IP4 127.0.0.1",
        "s=call",
        "c=IN IP4 127.0.0.1",
        "t=0 0",
        f"m=audio {port} RTP/AVP {methods}",
        "a=rtpmap:0 PCMU/8000",
    ]
    if include_dtmf:
        lines.extend(
            [
                "a=rtpmap:101 telephone-event/8000",
                "a=fmtp:101 0-15",
            ]
        )
    lines.append("a=sendrecv")
    return "\r\n".join(lines) + "\r\n"


def sdp_with_g729():
    return (
        "v=0\r\n"
        "o=caller 1 1 IN IP4 127.0.0.1\r\n"
        "s=call\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        "m=audio 40000 RTP/AVP 18\r\n"
        "a=rtpmap:18 G729/8000\r\n"
        "a=sendrecv\r\n"
    )


def incoming_invite(*, call_id="incoming-call@example.test", body=None):
    return sip_message(
        "INVITE sip:1001@127.0.0.1 SIP/2.0",
        [
            "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKin",
            "Max-Forwards: 70",
            "From: <sip:2002@127.0.0.1>;tag=remote",
            "To: <sip:1001@127.0.0.1>",
            f"Call-ID: {call_id}",
            "CSeq: 1 INVITE",
            "Contact: <sip:2002@127.0.0.1:5060>",
        ],
        body or sdp_with_pcmu(),
    )


def remote_ok_response(*, call_id, cseq, from_tag):
    return sip_message(
        "SIP/2.0 200 OK",
        [
            "Via: SIP/2.0/UDP 127.0.0.1:5062;branch=z9hG4bKout",
            f"From: <sip:1001@127.0.0.1>;tag={from_tag}",
            "To: <sip:2002@127.0.0.1>;tag=remote-ok",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq} INVITE",
            "Contact: <sip:2002@127.0.0.1:5060>",
        ],
        sdp_with_pcmu(port=42000, include_dtmf=False),
    )


def test_incoming_call_can_be_answered_used_and_hung_up(monkeypatch):
    observed_calls = []
    phone, responses, raw_messages = make_phone(
        monkeypatch,
        rtp_port=30000,
        callback=observed_calls.append,
    )

    phone.callback(incoming_invite())

    assert len(observed_calls) == 1
    call = observed_calls[0]
    assert call.state is VoIPModule.CallState.RINGING
    assert phone.has_call_id(call.call_id)
    assert phone.assignedPorts == [30000]
    assert responses[0].startswith("SIP/2.0 180 Ringing")

    assert len(call.RTPClients) == 1
    rtp = call.RTPClients[0]
    assert rtp.inPort == 30000
    assert rtp.outIP == "127.0.0.1"
    assert rtp.outPort == 40000

    call.answer()

    assert call.state is VoIPModule.CallState.ANSWERED
    assert rtp.started
    assert responses[-1].startswith("SIP/2.0 200 OK")
    assert "m=audio 30000 RTP/AVP 0 101" in responses[-1]
    assert "a=sendrecv" in responses[-1]

    call.write_audio(b"abc")
    assert rtp.writes == [b"abc"]
    assert call.read_audio(4, blocking=False) == b"\x80" * 4
    assert call.send_dtmf("12#") is True
    assert rtp.dtmf_sequences == ["12#"]

    call.hangup()

    assert call.state is VoIPModule.CallState.ENDED
    assert rtp.stopped
    assert phone.assignedPorts == []
    assert not phone.has_call_id(call.call_id)
    assert any(message.startswith("BYE ") for message, _target in raw_messages)


def test_incoming_invite_without_supported_audio_codec_is_rejected(monkeypatch):
    phone, responses, _raw_messages = make_phone(monkeypatch)

    phone.callback(
        incoming_invite(
            call_id="unsupported-codec@example.test",
            body=sdp_with_g729(),
        )
    )

    assert responses[-1].startswith("SIP/2.0 488 Not Acceptable Here")
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert FakeRTPClient.instances == []


def test_outbound_call_applies_pending_ok_response_and_sends_ack(monkeypatch):
    phone, _responses, raw_messages = make_phone(monkeypatch, rtp_port=31000)
    invite_request = {}

    def invite(number, media, sendtype):
        call_id = "outbound-call@example.test"
        session_id = 42
        request = phone.sip.gen_invite(
            number,
            str(session_id),
            media,
            sendtype,
            "z9hG4bKoutbound",
            call_id,
        )
        invite_request["message"] = SIP.SIPMessage(request.encode("utf8"))
        return invite_request["message"], call_id, session_id

    def pop_pending_invite_response(call_id):
        request = invite_request["message"]
        return remote_ok_response(
            call_id=call_id,
            cseq=request.headers["CSeq"]["check"],
            from_tag=request.headers["From"]["tag"],
        )

    monkeypatch.setattr(phone.sip, "invite", invite)
    monkeypatch.setattr(
        phone.sip,
        "pop_pending_invite_response",
        pop_pending_invite_response,
    )

    call = phone.call("2002")

    assert call.state is VoIPModule.CallState.ANSWERED
    assert phone.has_call_id("outbound-call@example.test")
    assert phone.assignedPorts == [31000]
    assert len(call.RTPClients) == 1

    rtp = call.RTPClients[0]
    assert rtp.started
    assert rtp.inPort == 31000
    assert rtp.outIP == "127.0.0.1"
    assert rtp.outPort == 42000
    assert rtp.preference is RTP.PayloadType.PCMU

    ack_messages = [
        message for message, _target in raw_messages if message.startswith("ACK ")
    ]
    assert len(ack_messages) == 1
    assert "CSeq: 1 ACK" in ack_messages[0]