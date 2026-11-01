import warnings

import pytest

from rfcvoip import SIP
from rfcvoip.VoIP import CallState
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_second_audit import outgoing_call


@pytest.mark.parametrize("missing", ["Contact", "To", "To-tag"])
def test_malformed_final_invite_response_releases_local_call(
    phone,
    monkeypatch,
    missing,
):
    call, response = outgoing_call(phone, monkeypatch)
    if missing == "To-tag":
        response.headers["To"]["tag"] = ""
    else:
        response.headers.pop(missing)
    monkeypatch.setattr(phone, "_send_ack", lambda _request: None)
    monkeypatch.setattr(phone.sip, "bye", lambda _request: None)

    with pytest.warns(RuntimeWarning, match="RTP"):
        phone.callback(response)

    assert call.state is CallState.ENDED
    assert phone.calls == {}
    assert phone.assignedPorts == []


@pytest.mark.parametrize("status", [401, 404, 503])
def test_stale_failure_for_previous_invite_does_not_end_current_call(
    phone,
    monkeypatch,
    status,
):
    call, response = outgoing_call(phone, monkeypatch)
    call.request.headers["CSeq"]["check"] = "2"
    response.headers["CSeq"]["check"] = "1"
    response.status = SIP.SIPStatus(status)
    acks = []
    monkeypatch.setattr(phone, "_send_ack", acks.append)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        phone.callback(response)

    assert call.state is CallState.DIALING
    assert phone._get_call(call.call_id) is call
    assert phone.assignedPorts
    assert acks == [response]


@pytest.mark.parametrize(
    "action", ["not_found", "unavailable", "unsupported-codec"]
)
def test_peer_input_does_not_replace_application_warning_filters(
    phone, action
):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        filters = list(warnings.filters)
        if action == "unsupported-codec":
            request = invite()
            request.body["m"][0]["methods"].append("invalid")
            phone._create_Call(request, 123)
        else:
            call = ringing_call(phone)
            getattr(call, action)(invite())

        assert warnings.filters == filters
