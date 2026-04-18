import unittest

from pyVoIP.SIP import SIPClient, SIPMessage


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, payload, target):
        self.sent.append((payload, target))


class DummyPhone:
    def __init__(self):
        self.calls = {}


class UnknownTransactionTests(unittest.TestCase):
    def make_client(self, phone=None, with_callback=True):
        if phone is None:
            phone = DummyPhone()
        self.callback_calls = []

        def callback(message):
            self.callback_calls.append(message)

        client = SIPClient(
            "sip.example.com",
            5060,
            "alice",
            "secret",
            phone=phone,
            myIP="127.0.0.1",
            myPort=5060,
            callCallback=callback if with_callback else None,
        )
        client.out = FakeSocket()
        return client, phone

    def make_request(self, method, call_id="call-123"):
        raw = (
            f"{method} sip:alice@sip.example.com SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.0.2.20:5090;branch=z9hG4bK123;rport\r\n"
            "From: <sip:bob@sip.example.com>;tag=remote-tag\r\n"
            "To: <sip:alice@sip.example.com>;tag=local-tag\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 1 {method}\r\n"
            "Contact: <sip:bob@192.0.2.20:5090>\r\n"
            "Content-Length: 0\r\n\r\n"
        )
        return SIPMessage(raw.encode("utf8"))

    def test_bye_without_callback_returns_481(self):
        client, _phone = self.make_client(with_callback=False)
        message = self.make_request("BYE")

        client.parse_message(message)

        self.assertEqual(len(self.callback_calls), 0)
        self.assertEqual(len(client.out.sent), 1)
        payload, _target = client.out.sent[0]
        self.assertIn(b"SIP/2.0 481 Call/Transaction Does Not Exist", payload)

    def test_bye_for_unknown_call_returns_481(self):
        client, _phone = self.make_client()
        message = self.make_request("BYE")

        client.parse_message(message)

        self.assertEqual(len(self.callback_calls), 0)
        self.assertEqual(len(client.out.sent), 1)
        payload, target = client.out.sent[0]
        self.assertIn(b"SIP/2.0 481 Call/Transaction Does Not Exist", payload)
        self.assertEqual(target, ("192.0.2.20", 5090))

    def test_cancel_for_unknown_call_returns_481(self):
        client, _phone = self.make_client()
        message = self.make_request("CANCEL")

        client.parse_message(message)

        self.assertEqual(len(self.callback_calls), 0)
        self.assertEqual(len(client.out.sent), 1)
        payload, _target = client.out.sent[0]
        self.assertIn(b"SIP/2.0 481 Call/Transaction Does Not Exist", payload)

    def test_bye_for_known_call_keeps_existing_200_ok_behavior(self):
        client, phone = self.make_client()
        phone.calls["call-123"] = object()
        message = self.make_request("BYE")

        client.parse_message(message)

        self.assertEqual(len(self.callback_calls), 1)
        self.assertEqual(len(client.out.sent), 1)
        payload, _target = client.out.sent[0]
        self.assertIn(b"SIP/2.0 200 OK", payload)

    def test_cancel_for_known_call_keeps_existing_200_ok_behavior(self):
        client, phone = self.make_client()
        phone.calls["call-123"] = object()
        message = self.make_request("CANCEL")

        client.parse_message(message)

        self.assertEqual(len(self.callback_calls), 1)
        self.assertEqual(len(client.out.sent), 1)
        payload, _target = client.out.sent[0]
        self.assertIn(b"SIP/2.0 200 OK", payload)


if __name__ == "__main__":
    unittest.main()
