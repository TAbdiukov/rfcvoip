import unittest
import warnings

from pyVoIP.VoIP.VoIP import CallState, VoIPCall


class FakePhone:
    def __init__(self):
        self.calls = {}
        self.released_call = None

    def release_ports(self, call=None):
        self.released_call = call


class FakeRTPClient:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeRequest:
    headers = {"To": {"number": "1000"}}


def make_call(state):
    phone = FakePhone()
    call = object.__new__(VoIPCall)
    call.state = state
    call.call_id = "call-1"
    call.RTPClients = [FakeRTPClient()]
    call.phone = phone
    phone.calls[call.call_id] = call
    return call, phone


class FinalInviteFailureTests(unittest.TestCase):
    def test_not_found_ends_ringing_call(self):
        call, phone = make_call(CallState.RINGING)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            call.not_found(FakeRequest())

        self.assertIs(call.state, CallState.ENDED)
        self.assertIs(call.RTPClients[0].stopped, True)
        self.assertIs(phone.released_call, call)
        self.assertNotIn(call.call_id, phone.calls)

    def test_unavailable_ends_ringing_call(self):
        call, phone = make_call(CallState.RINGING)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            call.unavailable(FakeRequest())

        self.assertIs(call.state, CallState.ENDED)
        self.assertIs(call.RTPClients[0].stopped, True)
        self.assertIs(phone.released_call, call)
        self.assertNotIn(call.call_id, phone.calls)

    def test_not_found_ignores_answered_call(self):
        call, phone = make_call(CallState.ANSWERED)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            call.not_found(FakeRequest())

        self.assertIs(call.state, CallState.ANSWERED)
        self.assertIs(call.RTPClients[0].stopped, False)
        self.assertIsNone(phone.released_call)
        self.assertIn(call.call_id, phone.calls)

    def test_unavailable_ignores_answered_call(self):
        call, phone = make_call(CallState.ANSWERED)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            call.unavailable(FakeRequest())

        self.assertIs(call.state, CallState.ANSWERED)
        self.assertIs(call.RTPClients[0].stopped, False)
        self.assertIsNone(phone.released_call)
