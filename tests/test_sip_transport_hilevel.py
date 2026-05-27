import socket

import pytest

import rfcvoip.SIPTransport as sip_transport_module
from rfcvoip.SIPTransport import (
    ResolvedSIPTarget,
    SIPConnection,
    SIPFramingError,
    SIPResolver,
    SIPTransport,
    format_hostport,
    split_hostport,
)


class _FakeNaptr:
    def __init__(
        self,
        service,
        replacement,
        *,
        order=10,
        preference=0,
        flags="s",
    ):
        self.service = service
        self.replacement = replacement
        self.order = order
        self.preference = preference
        self.flags = flags


class _FakeSrv:
    def __init__(
        self,
        target,
        port,
        *,
        priority=10,
        weight=0,
    ):
        self.target = target
        self.port = port
        self.priority = priority
        self.weight = weight


class _FakeDnsResolver:
    def __init__(self, records):
        self.records = records

    def resolve(self, name, record_type):
        key = (str(name).rstrip("."), str(record_type).upper())
        if key not in self.records:
            raise RuntimeError(f"No fake DNS record for {key!r}")
        return self.records[key]


def test_hostport_helpers_handle_ipv6_literals_and_default_ports():
    assert split_hostport("example.test:5070") == (
        "example.test",
        5070,
        True,
    )
    assert split_hostport("[2001:db8::1]:5070") == (
        "2001:db8::1",
        5070,
        True,
    )
    assert split_hostport("2001:db8::1", 5060) == (
        "2001:db8::1",
        5060,
        False,
    )

    assert format_hostport("example.test", 5060) == "example.test"
    assert format_hostport("example.test", 5070) == "example.test:5070"
    assert (
        format_hostport(
            "2001:db8::1",
            5060,
            always_include_port=True,
        )
        == "[2001:db8::1]:5060"
    )


def test_resolver_handles_uri_scheme_transport_and_legacy_host_port(
    monkeypatch,
):
    monkeypatch.setattr(sip_transport_module, "dns_resolver", None)
    resolver = SIPResolver()

    explicit_tcp = resolver.resolve(
        "sip:alice@example.test:5070;transport=tcp"
    )
    assert explicit_tcp.host == "example.test"
    assert explicit_tcp.port == 5070
    assert explicit_tcp.transport is SIPTransport.TCP
    assert explicit_tcp.explicit_port is True
    assert explicit_tcp.explicit_transport is True

    sips_default = resolver.resolve("sips:alice@example.test")
    assert sips_default.host == "example.test"
    assert sips_default.port == 5061
    assert sips_default.transport is SIPTransport.TLS

    legacy_host_and_port = resolver.resolve(
        "pbx.example.test",
        default_port=5080,
    )
    assert legacy_host_and_port.host == "pbx.example.test"
    assert legacy_host_and_port.port == 5080
    assert legacy_host_and_port.transport is SIPTransport.UDP
    assert legacy_host_and_port.explicit_port is True

    forced_tcp = resolver.resolve(
        "sip:example.test",
        default_transport=SIPTransport.TCP,
    )
    assert forced_tcp.host == "example.test"
    assert forced_tcp.port == 5060
    assert forced_tcp.transport is SIPTransport.TCP

    with pytest.raises(ValueError, match="SIPS URIs cannot use UDP"):
        resolver.resolve("sips:example.test;transport=udp")


def test_resolver_uses_naptr_order_then_srv_target(monkeypatch):
    monkeypatch.setattr(
        sip_transport_module,
        "dns_resolver",
        _FakeDnsResolver(
            {
                ("example.test", "NAPTR"): [
                    _FakeNaptr(
                        "SIP+D2U",
                        "_sip._udp.example.test.",
                        order=20,
                    ),
                    _FakeNaptr(
                        "SIP+D2T",
                        "_sip._tcp.example.test.",
                        order=10,
                    ),
                ],
                ("_sip._tcp.example.test", "SRV"): [
                    _FakeSrv("sip-tcp.example.test.", 5070),
                ],
                ("_sip._udp.example.test", "SRV"): [
                    _FakeSrv("sip-udp.example.test.", 5060),
                ],
            }
        ),
    )

    target = SIPResolver().resolve("sip:alice@example.test")

    assert target.host == "sip-tcp.example.test"
    assert target.port == 5070
    assert target.transport is SIPTransport.TCP
    assert target.source == "naptr"
    assert target.service == "SIP+D2T"
    assert target.uri_host == "example.test"


def test_resolver_falls_back_to_srv_when_naptr_is_not_usable(monkeypatch):
    monkeypatch.setattr(
        sip_transport_module,
        "dns_resolver",
        _FakeDnsResolver(
            {
                ("example.test", "NAPTR"): [
                    _FakeNaptr(
                        "SIPS+D2T",
                        "_sips._tcp.example.test.",
                        order=1,
                    ),
                ],
                ("_sip._tcp.example.test", "SRV"): [
                    _FakeSrv("sip-fallback.example.test.", 5080),
                ],
            }
        ),
    )

    target = SIPResolver().resolve("sip:example.test")

    assert target.host == "sip-fallback.example.test"
    assert target.port == 5080
    assert target.transport is SIPTransport.TCP
    assert target.source == "srv-fallback"
    assert target.service == "_sip._tcp.example.test"


def test_stream_connection_frames_messages_by_content_length():
    left, right = socket.socketpair()
    target = ResolvedSIPTarget(
        "example.test",
        5060,
        SIPTransport.TCP,
    )
    connection = SIPConnection("127.0.0.1", 0, target)
    connection.socket = left
    left.settimeout(1)
    right.settimeout(1)

    first = b"SIP/2.0 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    second = b"SIP/2.0 404 Not Found\r\nl: 0\r\n\r\n"

    try:
        right.sendall(b"\r\n" + first + second)

        assert connection.recv_raw_message() == first
        assert connection.recv_raw_message() == second
    finally:
        connection.close()
        right.close()


def test_stream_connection_rejects_conflicting_content_lengths():
    left, right = socket.socketpair()
    target = ResolvedSIPTarget(
        "example.test",
        5060,
        SIPTransport.TCP,
    )
    connection = SIPConnection("127.0.0.1", 0, target)
    connection.socket = left
    left.settimeout(1)
    right.settimeout(1)

    bad_frame = (
        b"SIP/2.0 200 OK\r\n"
        b"Content-Length: 1\r\n"
        b"l: 2\r\n"
        b"\r\n"
        b"x"
    )

    try:
        right.sendall(bad_frame)

        with pytest.raises(
            SIPFramingError,
            match="Conflicting SIP Content-Length",
        ):
            connection.recv_raw_message()
    finally:
        connection.close()
        right.close()


def test_udp_connection_sends_and_receives_local_datagrams():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    connection = None

    try:
        server.bind(("127.0.0.1", 0))
        server.settimeout(1)
        target = ResolvedSIPTarget(
            "127.0.0.1",
            server.getsockname()[1],
            SIPTransport.UDP,
        )

        connection = SIPConnection("127.0.0.1", 0, target)
        connection.open()
        assert connection.socket is not None
        connection.socket.settimeout(1)

        request = (
            b"OPTIONS sip:service@example.test SIP/2.0\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        connection.send(request)

        received, client_address = server.recvfrom(2048)
        assert received == request

        response = b"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n"
        server.sendto(response, client_address)

        assert connection.recv_raw_message() == response
        assert connection.last_recv_address == (
            server.getsockname()[0],
            server.getsockname()[1],
        )
    finally:
        if connection is not None:
            connection.close()
        server.close()