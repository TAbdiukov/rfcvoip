import pytest

from tests.test_voip_lifecycle_regressions import phone, ringing_call
from tests.test_voip_third_audit import reinvite


def test_failed_reinvite_rejection_remains_final_for_active_call(
    phone, monkeypatch
):
    call = ringing_call(phone)
    call.answer()
    request = reinvite(call, port=43000)
    request.body["m"][0]["methods"] = ["18"]
    send_response = phone.sip.send_response

    def fail_send(*args):
        raise OSError("response send failed")

    monkeypatch.setattr(phone.sip, "send_response", fail_send)
    with pytest.raises(OSError, match="response send failed"):
        phone.callback(request)
    assert phone._cached_invite_response(request).startswith("SIP/2.0 488 ")
    assert call.RTPClients[0].outPort == 40000
    monkeypatch.setattr(phone.sip, "send_response", send_response)

    # A transaction's committed final decision cannot change with a retry's
    # altered body, even though the original response failed to reach the peer.
    phone.callback(reinvite(call, port=43000))

    assert phone.responses[-1].startswith("SIP/2.0 488 ")
    assert call.RTPClients[0].outPort == 40000
