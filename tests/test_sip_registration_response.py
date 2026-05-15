from unittest.mock import patch

import pytest

from rfcvoip import SIP


class FakePhone:
    _status = None


class FakeSocket:
    def __init__(self, packets=None):
        self.packets = list(packets or [])
        self.blocking = True
        self.sent = []

    def recv(self, size):
        return self.packets.pop(0)

    def sendto(self, packet, target):
        self.sent.append((packet, target))

    def setblocking(self, blocking):
        self.blocking = blocking


def _header_value(request, header):
    prefix = header.lower() + ":"
    for line in request.split("\r\n"):
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _response_for(request, status):
    headers = []
    for header in ("Via", "From", "To", "Call-ID", "CSeq"):
        value = _header_value(request, header)
        if value:
            headers.append(f"{header}: {value}")
    return (
        f"SIP/2.0 {status}\r\n"
        + "\r\n".join(headers)
        + "\r\nContent-Length: 0\r\n\r\n"
    ).encode("utf8")


def _unrelated_register_response():
    return (
        "SIP/2.0 200 OK\r\n"
        "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKother\r\n"
        "From: <sip:user@example.com>;tag=from\r\n"
        "To: <sip:user@example.com>;tag=to\r\n"
        "Call-ID: unrelated@example.com\r\n"
        "CSeq: 1 REGISTER\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode("utf8")


def _client(packets):
    client = SIP.SIPClient(
        "example.com",
        5060,
        "user",
        "password",
        phone=FakePhone(),
        myIP="127.0.0.1",
    )
    client.NSD = True
    client.register_timeout = 0.01
    fake_socket = FakeSocket(packets)
    client.s = fake_socket
    client.out = fake_socket
    return client, fake_socket


def _fake_select(socket):
    def fake_select(readable, writable, exceptional, timeout=None):
        return ([socket], [], []) if socket.packets else ([], [], [])

    return fake_select


def test_register_response_wait_ignores_unrelated_and_provisional():
    client, fake_socket = _client([])
    request = client.gen_first_response()
    fake_socket.packets.extend(
        [
            _unrelated_register_response(),
            _response_for(request, "100 Trying"),
            _response_for(request, "200 OK"),
        ]
    )

    with patch("rfcvoip.SIP.select.select", side_effect=_fake_select(fake_socket)):
        response = client._wait_for_transaction_response(
            request, action="Registering"
        )

    assert response.status == SIP.SIPStatus.OK
    assert response.headers["Call-ID"] == _header_value(request, "Call-ID")


def test_register_response_wait_times_out_after_provisional_only():
    client, fake_socket = _client([])
    request = client.gen_first_response()
    fake_socket.packets.append(_response_for(request, "100 Trying"))

    with patch("rfcvoip.SIP.select.select", side_effect=_fake_select(fake_socket)):
        with pytest.raises(TimeoutError, match="only sent provisional"):
            client._wait_for_transaction_response(
                request, action="Registering"
            )


def test_trying_timeout_check_times_out_without_unbound_local_error():
    client, fake_socket = _client([])
    request = client.gen_first_response()
    response = SIP.SIPMessage(_response_for(request, "100 Trying"))

    with patch("rfcvoip.SIP.select.select", side_effect=_fake_select(fake_socket)):
        with pytest.raises(TimeoutError, match="still TRYING"):
            client.trying_timeout_check(response)
