import warnings

import pytest

from rfcvoip import RTP
from rfcvoip.VoIP import CallState
from rfcvoip.VoIP.VoIP import _sip_uri_user
from tests.test_voip_hilevel import remote_ok_response
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_second_audit import outgoing_call


def reinvite(call, *, cseq=2, branch="z9hG4bKreinvite", port=42000):
    request = invite()
    request.headers["To"]["tag"] = call.sip.tagLibrary[call.call_id]
    request.headers["CSeq"]["check"] = str(cseq)
    request.headers["Via"][0]["branch"] = branch
    request.body["m"][0]["port"] = port
    return request


@pytest.mark.parametrize("header", ["From", "To"])
def test_invite_with_wrong_dialog_tag_cannot_redirect_media(phone, header):
    call = ringing_call(phone)
    call.answer()
    request = reinvite(call)
    request.headers[header]["tag"] = "another-dialog"

    phone.callback(request)

    assert phone.responses[-1].startswith("SIP/2.0 481 ")
    assert call.RTPClients[0].outPort == 40000
    assert call.state is CallState.ANSWERED


def test_ringing_invite_retransmission_repeats_ringing_with_same_tag(phone):
    call = ringing_call(phone)
    tag = phone.sip.tagLibrary[call.call_id]

    phone.callback(invite())

    assert phone.responses[-1].startswith("SIP/2.0 180 ")
    assert phone.sip.tagLibrary[call.call_id] == tag
    assert call.state is CallState.RINGING


def test_original_invite_retransmission_does_not_undo_renegotiation(phone):
    call = ringing_call(phone)
    call.answer()
    original_answer = phone.responses[-1]
    phone.callback(reinvite(call))
    assert call.RTPClients[0].outPort == 42000

    phone.callback(invite())

    assert call.RTPClients[0].outPort == 42000
    assert phone.responses[-1] == original_answer


def test_stale_reinvite_does_not_roll_back_remote_target(phone):
    call = ringing_call(phone)
    call.answer()
    phone.callback(reinvite(call, cseq=3, branch="z9hG4bKnew"))

    phone.callback(reinvite(call, cseq=2, branch="z9hG4bKstale", port=43000))

    assert phone.responses[-1].startswith("SIP/2.0 500 ")
    assert call.RTPClients[0].outPort == 42000


def test_duplicate_reinvite_repeats_answer_without_applying_changed_body(
    phone,
):
    call = ringing_call(phone)
    call.answer()
    phone.callback(reinvite(call))
    answer = phone.responses[-1]

    phone.callback(reinvite(call, port=43000))

    assert call.RTPClients[0].outPort == 42000
    assert phone.responses[-1] == answer


def test_overlapping_ok_responses_only_start_rtp_once(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    duplicate = remote_ok_response(
        call_id=call.call_id,
        cseq=call.request.headers["CSeq"]["check"],
        from_tag=call.request.headers["From"]["tag"],
    )
    acks = []
    starts = []
    monkeypatch.setattr(phone, "_send_ack", acks.append)
    monkeypatch.setattr(phone.sip, "bye", lambda _request: None)

    def start(client):
        starts.append(client)
        if client.NSD:
            raise RuntimeError("RTP client already started")
        client.NSD = True
        if len(starts) == 1:
            phone.callback(duplicate)

    monkeypatch.setattr(RTP.RTPClient, "start", start)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        phone.callback(response)

    assert call.state is CallState.ANSWERED
    assert starts == call.RTPClients
    assert acks == [duplicate, response]
    assert phone._get_call(call.call_id) is call
    call._stop_rtp_clients()


@pytest.mark.parametrize(
    "user", ["alice;ext=2", "alice?desk", "alice;ext=2?desk"]
)
def test_sip_uri_user_preserves_legal_userinfo_separators(user):
    assert _sip_uri_user(f"sip:{user}@example.test;transport=tcp") == user


def test_sip_uri_user_ignores_password_and_header_address():
    assert (
        _sip_uri_user("sip:alice:secret@example.test?to=bob@example.test")
        == "alice"
    )
