from types import SimpleNamespace

from rfcvoip.VoIP.VoIP import CallState, VoIPPhone


class _Call:
    def __init__(self, phone, call_id="call-1", state=CallState.RINGING):
        self.phone = phone
        self.call_id = call_id
        self.state = state

    def _get_state(self):
        return self.state


class _Phone:
    def __init__(self):
        self.current_call = None

    def _get_call(self, call_id):
        if call_id == "call-1":
            return self.current_call
        return None


def test_delayed_frontend_callback_runs_for_current_call():
    phone = _Phone()
    call = _Call(phone)
    phone.current_call = call
    seen = []

    VoIPPhone._run_call_callback(seen.append, call, delay=0)

    assert seen == [call]


def test_delayed_frontend_callback_skips_removed_call():
    phone = _Phone()
    call = _Call(phone)
    seen = []

    VoIPPhone._run_call_callback(seen.append, call, delay=0)

    assert seen == []


def test_delayed_frontend_callback_skips_ended_call():
    phone = _Phone()
    call = _Call(phone, state=CallState.ENDED)
    phone.current_call = call
    seen = []

    VoIPPhone._run_call_callback(seen.append, call, delay=0)

    assert seen == []


def test_delayed_frontend_callback_skips_call_without_call_id():
    phone = _Phone()
    call = _Call(phone, call_id="")
    phone.current_call = call
    seen = []

    VoIPPhone._run_call_callback(seen.append, call, delay=0)

    assert seen == []


def test_inbound_invite_without_call_id_does_not_reach_frontend_callback():
    phone = VoIPPhone.__new__(VoIPPhone)
    frontend_calls = []
    responses = []

    phone.callCallback = frontend_calls.append
    phone.sip = SimpleNamespace(
        gen_response=lambda request, status: "SIP/2.0 400 Bad Request\r\n\r\n",
        send_response=lambda request, response: responses.append(response),
    )
    request = SimpleNamespace(headers={}, summary=lambda: "INVITE without Call-ID")

    VoIPPhone._callback_MSG_Invite(phone, request)

    assert frontend_calls == []
    assert responses == ["SIP/2.0 400 Bad Request\r\n\r\n"]