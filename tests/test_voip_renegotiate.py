import unittest
from types import SimpleNamespace

from rfcvoip import SIP
from rfcvoip.VoIP.VoIP import VoIPCall
from rfcvoip.VoIP.VoIP import VoIPPhone


class FakePhone:
    _has_compatible_rtp_address_family = (
        VoIPPhone._has_compatible_rtp_address_family
    )

    def __init__(self, my_ip: str):
        self.myIP = my_ip


class FakeSIP:
    def __init__(self):
        self.generated_answer = None
        self.generated_response = None
        self.sent = []

    def gen_answer(self, request, session_id, media, sendmode):
        self.generated_answer = (request, session_id, media, sendmode)
        return "answer"

    def gen_response(self, request, status):
        self.generated_response = (request, status)
        return f"response:{int(status)}"

    def send_response(self, request, message):
        self.sent.append((request, message))


class VoIPCallRenegotiateTest(unittest.TestCase):
    def _call(self) -> VoIPCall:
        call = VoIPCall.__new__(VoIPCall)
        call.phone = FakePhone("192.0.2.10")
        call.sip = FakeSIP()
        call.RTPClients = [
            SimpleNamespace(
                inPort=10000,
                assoc={0: object()},
                outIP="198.51.100.1",
                outPort=4000,
            )
        ]
        call.session_id = "123"
        call.sendmode = "sendrecv"
        call.call_id = "call-1"
        return call

    def _request(self, address_type: str, address: str, port: int = 5000):
        return SimpleNamespace(
            body={
                "c": [
                    {
                        "address_type": address_type,
                        "address": address,
                    }
                ],
                "m": [
                    {
                        "type": "audio",
                        "port": port,
                    }
                ],
            }
        )

    def test_renegotiate_rejects_mismatched_address_family(self):
        call = self._call()
        client = call.RTPClients[0]
        request = self._request("IP6", "2001:db8::1")

        call.renegotiate(request)

        self.assertEqual(client.outIP, "198.51.100.1")
        self.assertEqual(client.outPort, 4000)
        self.assertIsNone(call.sip.generated_answer)
        self.assertEqual(
            call.sip.generated_response,
            (request, SIP.SIPStatus.NOT_ACCEPTABLE_HERE),
        )
        self.assertEqual(call.sip.sent, [(request, "response:488")])


