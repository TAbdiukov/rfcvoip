import warnings

import pytest

from rfcvoip import SIP
from rfcvoip.VoIP import CallState, VoIPCall
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_second_audit import outgoing_call


@pytest.mark.parametrize("ending", ["cancel", "not_found", "hangup"])
def test_late_success_for_ended_outbound_call_is_acked_and_terminated(
    phone, monkeypatch, ending
):
    call, response = outgoing_call(phone, monkeypatch)
    acknowledged = []
    terminated = []
    monkeypatch.setattr(phone, "_send_ack", acknowledged.append)
    monkeypatch.setattr(phone.sip, "bye", terminated.append)
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    if ending == "hangup":
        phone.callback(response)
        call.hangup()
        acknowledged.clear()
        terminated.clear()
    elif ending == "not_found":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            call.not_found(response)
    else:
        call.cancel()

    phone.callback(response)

    assert acknowledged == [response]
    assert terminated == [response]
    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []


@pytest.mark.parametrize("during_cancel", [False, True])
@pytest.mark.parametrize("mismatch", ["sequence", "local_tag"])
def test_unrelated_success_cannot_use_an_ended_invite_to_send_bye(
    phone, monkeypatch, mismatch, during_cancel
):
    call, response = outgoing_call(phone, monkeypatch)
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    terminated = []
    monkeypatch.setattr(phone.sip, "bye", terminated.append)
    if mismatch == "sequence":
        response.headers["CSeq"]["check"] = "999"
    else:
        response.headers["From"]["tag"] = "unrelated"
    if during_cancel:
        monkeypatch.setattr(
            phone.sip, "cancel", lambda _request: phone.callback(response)
        )
        call.cancel()
    else:
        call.cancel()
        phone.callback(response)

    assert terminated == []


def test_ended_invite_cache_expires(phone, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("rfcvoip.VoIP.VoIP.time.monotonic", lambda: now[0])
    call, response = outgoing_call(phone, monkeypatch)
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    terminated = []
    monkeypatch.setattr(phone.sip, "bye", terminated.append)
    call.cancel()
    now[0] += 33.0

    phone.callback(response)

    assert terminated == []


def test_late_success_ack_retains_original_invite_authentication(
    phone, monkeypatch
):
    call, response = outgoing_call(phone, monkeypatch)
    authorization = (
        'Digest username="100", realm="realm", nonce="nonce", '
        'uri="sip:2002@127.0.0.1", response="response"'
    )
    original = call.request.raw.decode("utf8").replace(
        "\r\nContent-Length:",
        f"\r\nAuthorization: {authorization}\r\nContent-Length:",
        1,
    )
    call.request = SIP.SIPMessage(original.encode("utf8"))
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    sent = []
    monkeypatch.setattr(
        phone.sip, "send_raw", lambda raw, target: sent.append(raw)
    )
    call.cancel()

    phone.callback(response)

    assert sent[0].startswith(b"ACK ")
    assert f"Authorization: {authorization}\r\n".encode() in sent[0]
    assert sent[1].startswith(b"BYE ")


def test_answer_delivered_while_cancel_is_sending_is_terminated(
    phone, monkeypatch
):
    call, response = outgoing_call(phone, monkeypatch)
    acknowledged = []
    terminated = []
    monkeypatch.setattr(phone, "_send_ack", acknowledged.append)
    monkeypatch.setattr(phone.sip, "bye", terminated.append)
    monkeypatch.setattr(
        phone.sip, "cancel", lambda _request: phone.callback(response)
    )

    call.cancel()

    assert acknowledged == [response]
    assert terminated == [response]
    assert call.state is CallState.ENDED
    assert call.RTPClients == []
    assert phone.calls == {}
    assert phone.assignedPorts == []


def test_late_answer_wire_dialog_fields_without_global_tag_entry(
    phone, monkeypatch
):
    call, response = outgoing_call(phone, monkeypatch)
    original_tag = call.request.headers["From"]["tag"]
    original_sequence = int(call.request.headers["CSeq"]["check"])
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    call.cancel()
    phone.sip.tagLibrary.pop(call.call_id, None)
    sent = []
    monkeypatch.setattr(
        phone.sip, "send_raw", lambda raw, target: sent.append((raw, target))
    )

    phone.callback(response)

    assert len(sent) == 2
    ack, bye = [SIP.SIPMessage(raw) for raw, _target in sent]
    assert [ack.method, bye.method] == ["ACK", "BYE"]
    assert (
        ack.headers["From"]["tag"]
        == bye.headers["From"]["tag"]
        == original_tag
    )
    assert ack.headers["To"]["tag"] == bye.headers["To"]["tag"] == "remote-ok"
    assert int(ack.headers["CSeq"]["check"]) == original_sequence
    assert int(bye.headers["CSeq"]["check"]) > original_sequence
    assert ack.uri == bye.uri == "sip:2002@127.0.0.1:5060"
    assert [target for _raw, target in sent] == [("127.0.0.1", 5060)] * 2
    assert call.call_id not in phone.sip.tagLibrary


def test_retransmitted_late_answer_retries_failed_bye(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    call.cancel()
    sent = []
    failed = []

    def send(raw, target):
        message = SIP.SIPMessage(raw)
        sent.append(message)
        if message.method == "BYE" and not failed:
            failed.append(True)
            raise OSError("temporary BYE send failure")

    monkeypatch.setattr(phone.sip, "send_raw", send)

    phone.callback(response)
    phone.callback(SIP.SIPMessage(response.raw))

    assert [message.method for message in sent] == ["ACK", "BYE", "ACK", "BYE"]
    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []


def test_late_answer_still_ends_dialog_when_ack_send_fails(phone, monkeypatch):
    call, response = outgoing_call(phone, monkeypatch)
    monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
    call.cancel()
    sent = []

    def send(raw, target):
        message = SIP.SIPMessage(raw)
        sent.append(message)
        if message.method == "ACK":
            raise OSError("temporary ACK send failure")

    monkeypatch.setattr(phone.sip, "send_raw", send)

    phone.callback(response)

    assert [message.method for message in sent] == ["ACK", "BYE"]
    assert phone.calls == {}
    assert phone.assignedPorts == []


@pytest.mark.parametrize("origin", ["inbound", "outbound"])
def test_ended_call_releases_its_dialog_tag(phone, monkeypatch, origin):
    if origin == "inbound":
        call = ringing_call(phone)
        call.deny()
    else:
        call, _response = outgoing_call(phone, monkeypatch)
        monkeypatch.setattr(phone.sip, "cancel", lambda _request: None)
        call.cancel()

    assert call.call_id not in phone.sip.tagLibrary


def test_older_call_finalization_cannot_remove_replacement_dialog_tag(phone):
    old = ringing_call(phone)
    phone.sip.tagLibrary[old.call_id] = "replacement-local-tag"
    replacement = VoIPCall(
        phone,
        CallState.RINGING,
        invite(),
        124,
        phone.myIP,
        sendmode="sendrecv",
    )
    phone._set_call(old.call_id, replacement)

    old._set_state(CallState.ENDED)
    old._finalize_ended_call()

    assert phone._get_call(old.call_id) is replacement
    assert phone.sip.tagLibrary[old.call_id] == "replacement-local-tag"


def test_unusable_final_answer_preserves_dialog_fields_after_tag_cleanup(
    phone, monkeypatch
):
    call, response = outgoing_call(phone, monkeypatch)
    local_tag = call.request.headers["From"]["tag"]
    response.body["m"][0]["methods"] = ["18"]
    sent = []
    monkeypatch.setattr(
        phone.sip,
        "send_raw",
        lambda raw, target: sent.append(SIP.SIPMessage(raw)),
    )

    with pytest.warns(RuntimeWarning):
        phone.callback(response)

    assert [message.method for message in sent] == ["ACK", "BYE"]
    assert sent[1].headers["From"]["tag"] == local_tag
    assert sent[1].headers["To"]["tag"] == "remote-ok"
    assert call.call_id not in phone.sip.tagLibrary


def test_failed_inbound_setup_releases_tag_after_matching_final_response(
    phone,
):
    phone.request_ports(2, blocking=False)
    request = invite()

    phone._callback_MSG_Invite(request)

    ringing, rejected = [
        SIP.SIPMessage(raw.encode()) for raw in phone.responses
    ]
    assert int(ringing.status) == 180
    assert int(rejected.status) == 488
    assert ringing.headers["To"]["tag"] == rejected.headers["To"]["tag"]
    assert request.headers["Call-ID"] not in phone.sip.tagLibrary


def test_failed_ringing_send_releases_its_orphan_dialog_tag(
    phone, monkeypatch
):
    request = invite()

    def fail(*args):
        raise OSError("ringing send failed")

    monkeypatch.setattr(phone.sip, "send_response", fail)

    with pytest.raises(OSError, match="ringing send failed"):
        phone._callback_MSG_Invite(request)

    assert request.headers["Call-ID"] not in phone.sip.tagLibrary
    assert phone.session_ids == []


def test_callback_dispatch_failure_keeps_tag_until_rejection_is_built(
    phone, monkeypatch
):
    class FailedThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("callback dispatch failed")

    monkeypatch.setattr("rfcvoip.VoIP.VoIP.Thread", FailedThread)
    request = invite()

    with pytest.raises(RuntimeError, match="callback dispatch failed"):
        phone._callback_MSG_Invite(request)

    ringing, rejected = [
        SIP.SIPMessage(raw.encode()) for raw in phone.responses
    ]
    assert int(rejected.status) == 486
    assert ringing.headers["To"]["tag"] == rejected.headers["To"]["tag"]
    assert request.headers["Call-ID"] not in phone.sip.tagLibrary
