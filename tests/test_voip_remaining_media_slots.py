import copy
import warnings

import pytest

from rfcvoip import RTP, SIP
from rfcvoip.VoIP import CallState, InvalidStateError
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call


def mixed_offer():
    request = invite()
    accepted = request.body["m"][0]
    video = copy.deepcopy(accepted)
    video.update(type="video", port=0, methods=["31"])
    unsupported = copy.deepcopy(accepted)
    unsupported.update(port=40002, methods=["18"], attributes={})
    request.body["m"] = [video, unsupported, accepted]
    return request


def test_inbound_answer_retains_rejected_media_slots_in_offer_order(phone):
    request = mixed_offer()
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])

    call.answer()

    answer = SIP.SIPMessage(phone.responses[-1].encode("utf8"))
    assert [media["type"] for media in answer.body["m"]] == [
        "video",
        "audio",
        "audio",
    ]
    assert [media["port"] for media in answer.body["m"]] == [
        0,
        0,
        call.RTPClients[0].inPort,
    ]
    assert call.RTPClients[0].assoc == {0: RTP.PayloadType.PCMU}


def test_reinvite_keeps_initially_rejected_slots_and_active_target(phone):
    request = mixed_offer()
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])
    call.answer()
    update = copy.deepcopy(request)
    update.body["m"][2]["port"] = 45000

    call.renegotiate(update)

    assert phone.responses[-1].startswith("SIP/2.0 200 ")
    answer = SIP.SIPMessage(phone.responses[-1].encode("utf8"))
    assert [media["port"] for media in answer.body["m"]] == [
        0,
        0,
        call.RTPClients[0].inPort,
    ]
    assert call.RTPClients[0].outPort == 45000


def test_reinvite_cannot_reassign_a_live_stream_to_a_rejected_slot(phone):
    request = mixed_offer()
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])
    call.answer()
    update = copy.deepcopy(request)
    update.body["m"] = [update.body["m"][2]]
    update.body["m"][0]["port"] = 45000

    call.renegotiate(update)

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert call.RTPClients[0].outPort == 40000


def test_reinvite_cannot_merge_retained_media_groups(phone):
    request = invite(count=2)
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    phone._create_Call(request, 123)
    call = phone._get_call(request.headers["Call-ID"])
    call.answer()
    update = invite()
    update.body["session_connections"][0]["address_count"] = 2
    update.body["m"][0]["port_count"] = 2

    call.renegotiate(update)

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert [client.outPort for client in call.RTPClients] == [40000, 40002]


def test_overlapping_inbound_answer_cannot_end_the_committed_call(
    phone, monkeypatch
):
    call = ringing_call(phone)
    generate_answer = phone.sip.gen_answer
    entered = []
    declined = []
    outer_errors = []

    def overlap(*args):
        if not entered:
            entered.append(True)
            try:
                call.answer()
            except InvalidStateError:
                declined.append(True)
        return generate_answer(*args)

    monkeypatch.setattr(phone.sip, "gen_answer", overlap)
    try:
        call.answer()
    except InvalidStateError as exc:
        outer_errors.append(exc)

    assert outer_errors == []
    assert declined == [True]
    assert call.state is CallState.ANSWERED
    assert phone._get_call(call.call_id) is call
    assert phone.assignedPorts
    assert phone.session_ids == [123]
    assert (
        sum(
            response.startswith("SIP/2.0 200 ") for response in phone.responses
        )
        == 1
    )


def test_hangup_cleans_up_when_remote_contact_cannot_be_routed(phone):
    call = ringing_call(phone)
    call.answer()
    call.request.headers["Contact"] = "<sip:>"

    with pytest.raises(ValueError):
        call.hangup()

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []


def test_hangup_cleans_up_when_app_escalates_signaling_failure_warning(
    phone, monkeypatch
):
    call = ringing_call(phone)
    call.answer()

    def fail(*args):
        raise OSError("BYE send failed")

    monkeypatch.setattr(phone.sip, "bye", fail)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(RuntimeWarning, match="Failed to send SIP BYE"):
            call.hangup()

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []
