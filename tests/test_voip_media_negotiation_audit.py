import copy
import struct

import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState
from tests.test_voip_lifecycle_regressions import invite, phone
from tests.test_voip_second_audit import outgoing_call


def multi_stream_call(phone):
    request = invite(count=2)
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    return phone._get_call(request.headers["Call-ID"])


def test_initial_answer_preserves_each_stream_direction(phone):
    call = multi_stream_call(phone)

    call.answer()

    answer = SIP.SIPMessage(phone.responses[-1].encode("utf8"))
    expected = [RTP.TransmitType.SENDRECV, RTP.TransmitType.RECVONLY]
    assert [client.sendrecv for client in call.RTPClients] == expected
    assert [media["transmit_type"] for media in answer.body["m"]] == expected


def test_reinvite_preserves_each_stream_direction(phone):
    call = multi_stream_call(phone)
    call.answer()
    request = invite("sendonly", count=2)
    request.body["m"][1]["transmit_type"] = RTP.TransmitType.RECVONLY

    call.renegotiate(request)

    answer = SIP.SIPMessage(phone.responses[-1].encode("utf8"))
    expected = [RTP.TransmitType.RECVONLY, RTP.TransmitType.SENDONLY]
    assert [client.sendrecv for client in call.RTPClients] == expected
    assert [media["transmit_type"] for media in answer.body["m"]] == expected


def test_failed_reinvite_preserves_each_previous_stream_direction(
    phone, monkeypatch
):
    call = multi_stream_call(phone)
    call.answer()
    request = invite("sendonly", count=2)
    request.body["m"][1]["transmit_type"] = RTP.TransmitType.RECVONLY

    def fail(*args):
        raise OSError("answer response failed")

    monkeypatch.setattr(phone.sip, "send_response", fail)

    with pytest.raises(OSError, match="answer response failed"):
        call.renegotiate(request)

    assert [client.sendrecv for client in call.RTPClients] == [
        RTP.TransmitType.SENDRECV,
        RTP.TransmitType.RECVONLY,
    ]


@pytest.mark.parametrize("added_type", ["audio", "video"])
def test_outbound_answer_cannot_add_an_unoffered_media_stream(
    phone, monkeypatch, added_type
):
    call, response = outgoing_call(phone, monkeypatch)
    added = copy.deepcopy(response.body["m"][0])
    added["type"] = added_type
    added["port"] += 2
    response.body["m"].append(added)
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    monkeypatch.setattr(phone.sip, "bye", lambda _request: None)

    with pytest.warns(RuntimeWarning, match="RTP"):
        phone.callback(response)

    assert call.state is CallState.ENDED
    assert phone.assignedPorts == []
    assert phone.calls == {}


def remapped_outbound_call(phone, monkeypatch):
    monkeypatch.setattr(
        phone, "_default_audio_offer", lambda: {96: RTP.PayloadType.PCMU}
    )
    call, response = outgoing_call(phone, monkeypatch)
    media = response.body["m"][0]
    media["methods"] = ["97"]
    media["attributes"] = {
        "97": {"rtpmap": {"name": "PCMU", "frequency": "8000"}}
    }
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    phone.callback(response)
    return call


def test_remapped_answer_keeps_original_receive_payload_type(
    phone, monkeypatch
):
    call = remapped_outbound_call(phone, monkeypatch)
    client = call.RTPClients[0]
    client.parse_packet(
        struct.pack("!BBHII", 0x80, 96, 1, 100, 1234) + b"\xff" * 160
    )

    assert call.state is CallState.ANSWERED
    assert client.assoc == {97: RTP.PayloadType.PCMU}
    assert client.receive_assoc == {96: RTP.PayloadType.PCMU}
    assert client.pmin.available() == 160


@pytest.mark.parametrize("send_failure", [False, True])
def test_reinvite_updates_remapped_receive_payload_after_successful_answer(
    phone, monkeypatch, send_failure
):
    call = remapped_outbound_call(phone, monkeypatch)
    request = invite()
    request.headers["Call-ID"] = call.call_id
    request.body["m"][0]["methods"] = ["97"]
    request.body["m"][0]["attributes"] = {
        "97": {"rtpmap": {"name": "PCMU", "frequency": "8000"}}
    }
    if send_failure:

        def fail(*args):
            raise OSError("answer response failed")

        monkeypatch.setattr(phone.sip, "send_response", fail)
        with pytest.raises(OSError, match="answer response failed"):
            call.renegotiate(request)
    else:
        call.renegotiate(request)

    payload_type = 96 if send_failure else 97
    client = call.RTPClients[0]
    client.parse_packet(
        struct.pack("!BBHII", 0x80, payload_type, 1, 100, 1234) + b"\xff" * 160
    )
    assert client.receive_assoc == {payload_type: RTP.PayloadType.PCMU}
    assert client.pmin.available() == 160


def test_outbound_answer_requires_a_common_offered_audio_codec(
    phone, monkeypatch
):
    monkeypatch.setattr(
        phone, "_default_audio_offer", lambda: {0: RTP.PayloadType.PCMU}
    )
    call, response = outgoing_call(phone, monkeypatch)
    response.body["m"][0]["methods"] = ["8"]
    response.body["m"][0]["attributes"] = {}
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    monkeypatch.setattr(phone.sip, "bye", lambda _request: None)

    with pytest.warns(RuntimeWarning, match="RTP"):
        phone.callback(response)

    assert call.state is CallState.ENDED
    assert phone.assignedPorts == []
    assert phone.calls == {}


def test_outbound_ack_carries_invite_authentication(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    authorization = (
        'Digest username="100", realm="realm", nonce="nonce", '
        'uri="sip:2002@127.0.0.1", response="response"'
    )
    original = call.request.raw.decode("utf8")
    original = original.replace(
        "\r\nContent-Length:",
        f"\r\nAuthorization: {authorization}\r\nContent-Length:",
        1,
    )
    call.request = SIP.SIPMessage(original.encode("utf8"))
    sent = []
    monkeypatch.setattr(
        phone.sip, "send_raw", lambda data, target: sent.append(data)
    )

    phone._send_ack(response)

    assert (
        b"Authorization: " + authorization.encode("utf8") + b"\r\n" in sent[0]
    )
