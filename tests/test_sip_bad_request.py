import socket
import threading

import pytest

from rfcvoip.SIP import SIPClient
from rfcvoip.SIP import SIPRequestError
from rfcvoip.VoIP.status import PhoneStatus


class DummyPhone:
    def __init__(self):
        self._status = PhoneStatus.INACTIVE


def _header(request: str, name: str) -> str:
    prefix = f"{name.lower()}:"
    for line in request.split("\r\n"):
        if line.lower().startswith(prefix):
            return line
    raise AssertionError(f"Missing {name} header in request")


def _bad_request_response(request: bytes) -> bytes:
    request_text = request.decode("utf8")
    to_header = _header(request_text, "To")
    if ";tag=" not in to_header.lower():
        to_header += ";tag=badreq"

    response = "\r\n".join(
        [
            "SIP/2.0 400 Bad Request",
            _header(request_text, "Via"),
            _header(request_text, "From"),
            to_header,
            _header(request_text, "Call-ID"),
            _header(request_text, "CSeq"),
            "Content-Length: 0",
            "",
            "",
        ]
    )
    return response.encode("utf8")


def _serve_one_bad_request(sock: socket.socket) -> None:
    request, address = sock.recvfrom(8192)
    sock.sendto(_bad_request_response(request), address)


def _client_for(server_port: int) -> SIPClient:
    client = SIPClient(
        "127.0.0.1",
        server_port,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP="127.0.0.1",
        myPort=0,
    )
    client.NSD = True
    client.register_timeout = 1
    client.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.s.bind(("127.0.0.1", 0))
    client.myPort = client.s.getsockname()[1]
    client.out = client.s
    return client


def test_register_bad_request_raises_sip_request_error():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    client = _client_for(server.getsockname()[1])
    thread = threading.Thread(
        target=_serve_one_bad_request,
        args=(server,),
        daemon=True,
    )

    try:
        thread.start()
        with pytest.raises(SIPRequestError) as excinfo:
            client._SIPClient__register()
    finally:
        client.s.close()
        server.close()
        thread.join(timeout=1)

    message = str(excinfo.value)
    assert "SIP REGISTER failed with 400 Bad Request" in message
    assert "Call-ID=" in message
    assert "CSeq=1 REGISTER" in message


def test_deregister_bad_request_raises_sip_request_error():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    client = _client_for(server.getsockname()[1])
    thread = threading.Thread(
        target=_serve_one_bad_request,
        args=(server,),
        daemon=True,
    )

    try:
        thread.start()
        with pytest.raises(SIPRequestError) as excinfo:
            client._SIPClient__deregister()
    finally:
        client.s.close()
        server.close()
        thread.join(timeout=1)

    message = str(excinfo.value)
    assert "SIP DEREGISTER failed with 400 Bad Request" in message
    assert "Call-ID=" in message
    assert "CSeq=1 REGISTER" in message
