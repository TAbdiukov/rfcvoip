import socket
from types import SimpleNamespace

import pytest

import rfcvoip.SIPTransport as transport_module
from rfcvoip.SIP import SIPClient, SIPMessage
from rfcvoip.SIPTransport import SIPConnection, SIPResolver


@pytest.mark.parametrize("maddr", ["192.0.2.40", "[2001:db8::40]"])
def test_maddr_changes_destination_without_replacing_uri_identity(
    monkeypatch, maddr
):
    monkeypatch.setattr(transport_module, "dns_resolver", None)
    uri = f"sips:alice@example.test:5070;maddr={maddr}"

    parsed = SIPResolver.parse_uri(uri)
    target = SIPResolver().resolve(uri)
    client = SIPClient(uri, None, "alice", "secret", phone=SimpleNamespace())
    raw = client.gen_first_response().encode()
    request = SIPMessage(raw)

    assert parsed.host == "example.test"
    assert target.host == maddr.strip("[]")
    assert target.port == 5070
    assert target.uri_host == "example.test"
    assert raw.startswith(b"REGISTER sips:example.test:5070 SIP/2.0\r\n")
    assert request.headers["To"]["host"] == "example.test:5070"


def test_tls_maddr_connects_to_override_and_verifies_original_domain(
    monkeypatch,
):
    names = []
    connected = []
    sock = SimpleNamespace(
        settimeout=lambda timeout: None,
        bind=lambda address: None,
        connect=lambda address: connected.append(address),
        close=lambda: None,
    )
    context = SimpleNamespace(
        wrap_socket=lambda sock, server_hostname: names.append(server_hostname)
        or sock
    )
    monkeypatch.setattr(transport_module.socket, "socket", lambda *args: sock)
    monkeypatch.setattr(
        transport_module.socket,
        "getaddrinfo",
        lambda host, port, *args: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", (host, port))
        ],
    )
    target = SIPResolver().resolve(
        "sips:alice@example.test:5070;maddr=192.0.2.40"
    )
    connection = SIPConnection("127.0.0.1", 0, target, tls_context=context)
    try:
        connection.open()

        assert connected == [("192.0.2.40", 5070)]
        assert names == ["example.test"]
    finally:
        connection.close()


def test_dns_maddr_queries_alternate_domain_but_retains_uri_identity(
    monkeypatch,
):
    queries = []

    def resolve(name, record_type):
        queries.append((name, record_type))
        if (name, record_type) == ("_sip._tcp.edge.test", "SRV"):
            return [
                SimpleNamespace(
                    target="proxy.test.", port=5080, priority=0, weight=0
                )
            ]
        raise RuntimeError("No test DNS record")

    monkeypatch.setattr(
        transport_module, "dns_resolver", SimpleNamespace(resolve=resolve)
    )

    target = SIPResolver().resolve("sip:alice@example.test;maddr=edge.test")

    assert queries == [
        ("edge.test", "NAPTR"),
        ("_sip._tcp.edge.test", "SRV"),
    ]
    assert target.host == "proxy.test"
    assert target.port == 5080
    assert target.uri_host == "example.test"
