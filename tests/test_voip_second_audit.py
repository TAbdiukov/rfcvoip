import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState, NoPortsAvailableError
from rfcvoip.VoIP.VoIP import (
    _expanded_media_connections,
    _media_rtp_targets,
    _payload_type_from_media_method,
)
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_hilevel import remote_ok_response


def test_exhausted_pool_does_not_reassign_a_reserved_port(phone):
    phone.rtpPortHigh = phone.rtpPortLow
    reserved = phone.request_port(blocking=False)

    with pytest.raises(NoPortsAvailableError):
        phone.request_port(blocking=False)

    assert phone.assignedPorts == [reserved]


def test_impossible_contiguous_reservation_does_not_wait(phone, monkeypatch):
    phone.NSD = True

    def unexpected_sleep(_duration):
        pytest.fail("An impossible reservation must fail without waiting")

    monkeypatch.setattr("rfcvoip.VoIP.VoIP.time.sleep", unexpected_sleep)

    with pytest.raises(NoPortsAvailableError):
        phone.request_ports(3)


def test_failed_inbound_construction_preserves_unrelated_reservation(
    phone,
    monkeypatch,
):
    phone.rtpPortHigh = phone.rtpPortLow + 2
    reserved = phone.request_port(blocking=False)
    factory = RTP.RTPClient
    created = []
    stopped = []

    def fail_second_client(*args, **kwargs):
        if created:
            raise RTP.RTPParseError("second media failed")
        client = factory(*args, **kwargs)
        client.stop = lambda: stopped.append(client)
        created.append(client)
        return client

    monkeypatch.setattr(RTP, "RTPClient", fail_second_client)

    phone._callback_MSG_Invite(invite(count=2))

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert phone.calls == {}
    assert phone.session_ids == []
    assert phone.assignedPorts == [reserved]
    assert stopped == created


def test_remote_cancel_cleans_up_even_if_final_response_fails(
    phone,
    monkeypatch,
):
    call = ringing_call(phone)

    def fail(*args):
        raise OSError("connection lost")

    monkeypatch.setattr(phone.sip, "send_response", fail)

    with pytest.raises(OSError, match="connection lost"):
        phone._callback_MSG_Cancel(invite())

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []


@pytest.mark.parametrize("failure_stage", ["start", "response"])
def test_answer_failure_cleans_up_started_media(
    phone, monkeypatch, failure_stage
):
    call = ringing_call(phone)
    client = call.RTPClients[0]
    stopped = []
    monkeypatch.setattr(client, "stop", lambda: stopped.append(client))

    def fail(*args):
        raise OSError("answer failed")

    if failure_stage == "start":
        monkeypatch.setattr(client, "start", fail)
    else:
        monkeypatch.setattr(phone.sip, "send_response", fail)

    with pytest.raises(OSError, match="answer failed"):
        call.answer()

    assert stopped == [client]
    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []


@pytest.mark.parametrize("method", ["-1", "128", "pcmu"])
def test_invalid_payload_number_cannot_be_enabled_by_rtpmap(method):
    media = {
        "attributes": {
            method: {
                "rtpmap": {
                    "name": "PCMU",
                    "frequency": "8000",
                }
            }
        }
    }

    with pytest.raises(ValueError):
        _payload_type_from_media_method(media, method)


def test_reclaimed_ended_call_cannot_later_release_reassigned_ports(phone):
    call = ringing_call(phone)
    call._set_state(CallState.ENDED)

    reserved = phone.request_ports(2, blocking=False)
    call.__del__()

    assert sorted(phone.assignedPorts) == sorted(reserved)


def outgoing_call(phone, monkeypatch):
    def originate(number, media, sendtype):
        message = phone.sip.gen_invite(
            number, "42", media, sendtype, "z9hG4bKout", "outgoing-call"
        )
        return SIP.SIPMessage(message.encode("utf8")), "outgoing-call", 42

    monkeypatch.setattr(phone.sip, "invite", originate)
    call = phone.call("2002")
    response = remote_ok_response(
        call_id=call.call_id,
        cseq=call.request.headers["CSeq"]["check"],
        from_tag=call.request.headers["From"]["tag"],
    )
    return call, response


def test_outbound_rtp_start_failure_ends_call_and_acks(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    acks = []
    monkeypatch.setattr(phone, "_send_ack", acks.append)
    monkeypatch.setattr(phone.sip, "bye", lambda _request: None)

    def fail(_client):
        raise OSError("RTP bind failed")

    monkeypatch.setattr(RTP.RTPClient, "start", fail)

    with pytest.warns(RuntimeWarning, match="RTP"):
        phone.callback(response)

    assert acks == [response]
    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []


@pytest.mark.parametrize("bad_sdp", ["family", "ports", "codec"])
def test_unusable_answer_is_cleaned_up_even_if_ack_fails(
    phone,
    monkeypatch,
    bad_sdp,
):
    call, response = outgoing_call(phone, monkeypatch)
    if bad_sdp == "family":
        response.body["session_connections"][0]["address_type"] = "IP6"
    elif bad_sdp == "ports":
        response.body["m"][0]["port_count"] = 2
    else:
        response.body["m"][0]["methods"] = ["18"]
    byes = []
    monkeypatch.setattr(phone.sip, "bye", byes.append)

    def fail(_request):
        raise OSError("ACK send failed")

    monkeypatch.setattr(phone, "_send_ack", fail)

    with pytest.warns(RuntimeWarning):
        phone.callback(response)

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert byes == [response]


def test_additional_ok_does_not_renegotiate_connected_call(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    acks = []
    monkeypatch.setattr(phone, "_send_ack", acks.append)
    phone.callback(response)
    assert call.state is CallState.ANSWERED
    clients = list(call.RTPClients)

    extra = remote_ok_response(
        call_id=call.call_id,
        cseq=call.request.headers["CSeq"]["check"],
        from_tag=call.request.headers["From"]["tag"],
    )
    extra.body["m"] = []
    phone.callback(extra)

    assert call.state is CallState.ANSWERED
    assert call.RTPClients == clients
    assert phone._get_call(call.call_id) is call
    assert acks == [response, extra]


def test_local_cancel_during_outbound_media_setup_cannot_resurrect_call(
    phone,
    monkeypatch,
):
    call, response = outgoing_call(phone, monkeypatch)
    acks = []
    byes = []
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    monkeypatch.setattr(phone.sip, "bye", byes.append)
    monkeypatch.setattr(phone, "_send_ack", acks.append)
    monkeypatch.setattr(RTP.RTPClient, "start", lambda _client: call.cancel())

    with pytest.warns(RuntimeWarning, match="RTP"):
        phone.callback(response)

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert acks == [response]
    assert byes == [response]


def test_media_address_expansion_is_bounded_by_udp_port_space():
    request = invite()
    request.body["session_connections"][0]["address_count"] = 65536

    with pytest.raises(RTP.RTPParseError, match="port"):
        _expanded_media_connections(request, request.body["m"][0])


def test_media_targets_cannot_overflow_remote_udp_port_range():
    request = invite()
    media = request.body["m"][0]
    request.body["session_connections"][0]["address_count"] = 2

    with pytest.raises(RTP.RTPParseError, match="port"):
        _media_rtp_targets(request, media, 65535)
