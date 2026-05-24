from collections import deque
from threading import Lock
import unittest

from rfcvoip import RTP
from rfcvoip.VoIP.VoIP import VoIPCall


class _SequenceClient:
    def __init__(self, result=True):
        self.result = result
        self.sequences = []

    def send_dtmf_sequence(self, digits):
        self.sequences.append(digits)
        return self.result


class DTMFDispatchTest(unittest.TestCase):
    def test_normalize_dtmf_sequence(self):
        self.assertEqual(RTP.normalize_dtmf_sequence(" 1 2 # a "), "12#A")
        self.assertEqual(
            RTP.normalize_dtmf_sequence("12A", allow_abcd=False),
            "",
        )
        self.assertEqual(RTP.normalize_dtmf_sequence("12x"), "")

    def test_rtp_client_queues_dtmf_sequence(self):
        client = object.__new__(RTP.RTPClient)
        client._telephone_event_pt = 101
        client._dtmf_lock = Lock()
        client._pending_dtmf = deque()

        self.assertTrue(client.send_dtmf_sequence("1 2#"))
        self.assertEqual(list(client._pending_dtmf), ["1", "2", "#"])

    def test_rtp_client_rejects_sequence_without_telephone_event(self):
        client = object.__new__(RTP.RTPClient)
        client._telephone_event_pt = None
        client._dtmf_lock = Lock()
        client._pending_dtmf = deque()

        self.assertFalse(client.send_dtmf_sequence("12"))
        self.assertEqual(list(client._pending_dtmf), [])

    def test_call_dispatches_sequence_to_rtp_clients(self):
        client = _SequenceClient()
        call = object.__new__(VoIPCall)
        call.call_id = "call-id"
        call.RTPClients = [client]

        self.assertTrue(call.send_dtmf(" 12 # "))
        self.assertEqual(client.sequences, ["12#"])

    def test_call_can_reject_abcd(self):
        client = _SequenceClient()
        call = object.__new__(VoIPCall)
        call.call_id = "call-id"
        call.RTPClients = [client]

        self.assertFalse(call.send_dtmf("A", allow_abcd=False))
        self.assertEqual(client.sequences, [])


if __name__ == "__main__":
    unittest.main()