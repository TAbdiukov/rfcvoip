import pytest

from rfcvoip.VoIP import CallState
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_second_audit import outgoing_call
from tests.test_voip_third_audit import reinvite


@pytest.mark.parametrize("failure_stage", ["generate", "send"])
def test_ringing_response_failure_releases_inbound_session(
    phone, monkeypatch, failure_stage
):
    phone.session_ids.append(7)

    def fail(*args):
        raise OSError("ringing response failed")

    monkeypatch.setattr(
        phone.sip,
        "gen_ringing" if failure_stage == "generate" else "send_response",
        fail,
    )

    with pytest.raises(OSError, match="ringing response failed"):
        phone.callback(invite())

    assert phone.session_ids == [7]
    assert phone.calls == {}
    assert phone.assignedPorts == []


@pytest.mark.parametrize("origin", ["inbound", "outbound"])
def test_accepted_reinvite_refreshes_bye_destination(
    phone, monkeypatch, origin
):
    if origin == "inbound":
        call = ringing_call(phone)
        call.answer()
    else:
        call, response = outgoing_call(phone, monkeypatch)
        monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
        phone.callback(response)
    request = reinvite(call)
    request.headers["Call-ID"] = call.call_id
    if origin == "outbound":
        request.headers["From"]["tag"] = "remote-ok"
    request.headers["Contact"] = "<sip:200@127.0.0.2:5090>"
    sent = []
    monkeypatch.setattr(
        phone.sip, "send_raw", lambda data, target: sent.append((data, target))
    )

    phone.callback(request)
    assert phone.responses[-1].startswith("SIP/2.0 200 ")
    assert phone.sip._has_matching_call(request)
    call.hangup()

    assert sent[-1][0].startswith(b"BYE sip:200@127.0.0.2:5090 SIP/2.0\r\n")
    assert sent[-1][1] == ("127.0.0.2", 5090)
    assert call.state is CallState.ENDED


@pytest.mark.parametrize("failure_stage", ["rejection", "send"])
def test_unaccepted_reinvite_preserves_bye_destination(
    phone, monkeypatch, failure_stage
):
    call = ringing_call(phone)
    call.answer()
    original_contact = call.request.headers["Contact"]
    request = reinvite(call)
    request.headers["Contact"] = "<sip:200@127.0.0.2:5090>"
    if failure_stage == "rejection":
        request.body["m"][0]["methods"] = ["8"]
        phone.callback(request)
        assert phone.responses[-1].startswith("SIP/2.0 488 ")
    else:

        def fail(*args):
            raise OSError("answer response failed")

        monkeypatch.setattr(phone.sip, "send_response", fail)
        with pytest.raises(OSError, match="answer response failed"):
            phone.callback(request)

    assert call.request.headers["Contact"] == original_contact
    assert call.state is CallState.ANSWERED
