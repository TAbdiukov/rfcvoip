import pytest

from rfcvoip.VoIP import CallState, VoIPCall
from tests.test_voip_lifecycle_regressions import invite, phone, ringing_call
from tests.test_voip_third_audit import reinvite


def test_reinvite_retry_after_send_failure_applies_media_change(
    phone, monkeypatch
):
    call = ringing_call(phone)
    call.answer()
    request = reinvite(call, port=42000)
    send_response = phone.sip.send_response

    def fail_send(*args):
        raise OSError("temporary signaling failure")

    monkeypatch.setattr(phone.sip, "send_response", fail_send)
    with pytest.raises(OSError, match="temporary signaling failure"):
        phone.callback(request)
    assert call.RTPClients[0].outPort == 40000

    monkeypatch.setattr(phone.sip, "send_response", send_response)
    phone.callback(request)

    assert phone.responses[-1].startswith("SIP/2.0 200 ")
    assert call.RTPClients[0].outPort == 42000


def test_legacy_reinvite_without_branch_keeps_working(phone):
    request = invite()
    del request.headers["Via"][0]["branch"]
    phone.sip.tagLibrary[request.headers["Call-ID"]] = "local"
    call = VoIPCall(
        phone, CallState.RINGING, request, 123, phone.myIP, sendmode="sendrecv"
    )
    phone._set_call(call.call_id, call)
    call.answer()
    update = reinvite(call, port=42000)
    del update.headers["Via"][0]["branch"]

    phone.callback(update)

    assert phone.responses[-1].startswith("SIP/2.0 200 ")
    assert call.RTPClients[0].outPort == 42000
