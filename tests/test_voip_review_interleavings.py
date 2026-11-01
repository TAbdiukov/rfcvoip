import warnings

from rfcvoip.VoIP import CallState, InvalidStateError
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call


def test_mixed_noninteger_and_valid_payload_methods_do_not_abort_inbound_call(
    phone, monkeypatch
):
    request = invite()
    request.body["m"][0]["methods"] = ["invalid", "0"]
    # Avoid starting the delayed user callback; this test exercises dispatch.
    monkeypatch.setattr(phone, "_run_call_callback", lambda *args: None)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        phone._callback_MSG_Invite(request)

    call = phone._get_call(request.headers["Call-ID"])
    assert call is not None
    assert list(call.RTPClients[0].assoc) == [0]


def test_cancel_completed_during_answer_preparation_cannot_resurrect_call(
    phone, monkeypatch
):
    call = ringing_call(phone)
    cancelled = []
    generate_answer = phone.sip.gen_answer

    def deliver_cancel_during_preparation(*args):
        cancelled.append(True)
        phone._callback_MSG_Cancel(invite())
        assert call.state is CallState.ENDED
        return generate_answer(*args)

    monkeypatch.setattr(
        phone.sip, "gen_answer", deliver_cancel_during_preparation
    )
    try:
        call.answer()
    except InvalidStateError:
        pass

    assert cancelled
    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []
    assert phone.session_ids == []
    assert not any(
        response.startswith("SIP/2.0 200 ") for response in phone.responses
    )


def test_answer_committed_before_send_prevails_over_later_cancel(
    phone, monkeypatch
):
    call = ringing_call(phone)
    cancelled = []

    def deliver_cancel_during_send(request, message):
        # Once the answer is committed, a racing CANCEL cannot undo its 200.
        if message.startswith("SIP/2.0 200 ") and not cancelled:
            cancelled.append(True)
            phone._callback_MSG_Cancel(invite())
            assert call.state is CallState.ANSWERED
        phone.responses.append(message)

    monkeypatch.setattr(phone.sip, "send_response", deliver_cancel_during_send)
    call.answer()

    assert cancelled
    assert call.state is CallState.ANSWERED
    assert phone._get_call(call.call_id) is call
    assert phone.assignedPorts
    assert phone.session_ids == [123]
    assert not any(
        response.startswith("SIP/2.0 487 ") for response in phone.responses
    )
