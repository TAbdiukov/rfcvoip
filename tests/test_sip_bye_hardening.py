from types import SimpleNamespace

from rfcvoip.SIP import SIPClient


class DummyPhone:
    calls = {}
    _status = None


class FailingDialogConnection:
    def __init__(self):
        self.sent = []

    def send(self, data, target):
        self.sent.append(target)
        if target == ("172.17.0.2", 5060):
            raise OSError(22, "Invalid argument")


def test_udp_dialog_send_falls_back_to_signal_target_when_contact_unroutable():
    client = SIPClient(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP="127.0.0.1",
        myPort=5059,
    )
    connection = FailingDialogConnection()
    client.connection = connection

    client.send_raw(b"BYE test\r\n\r\n", ("172.17.0.2", 5060))

    assert connection.sent == [
        ("172.17.0.2", 5060),
        ("127.0.0.1", 5060),
    ]
