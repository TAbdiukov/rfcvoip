from types import SimpleNamespace

import pytest

import rfcvoip.SIPTransport as transport_module
from rfcvoip.SIPTransport import SIPResolver, SIPTransport


def srv(target, port=5060):
    return SimpleNamespace(target=target, port=port, priority=0, weight=0)


def dns(monkeypatch, records):
    queries = []

    def resolve(name, record_type):
        queries.append((name, record_type))
        if (name, record_type) not in records:
            raise RuntimeError("No test DNS record")
        return records[(name, record_type)]

    monkeypatch.setattr(
        transport_module, "dns_resolver", SimpleNamespace(resolve=resolve)
    )
    return queries


@pytest.mark.parametrize(
    "uri,service",
    [
        ("sip:example.test;transport=udp", "_sip._udp.example.test"),
        ("sip:example.test;transport=tcp", "_sip._tcp.example.test"),
        ("sips:example.test", "_sips._tcp.example.test"),
    ],
)
def test_explicitly_unavailable_service_cannot_fall_back_to_domain(
    monkeypatch, uri, service
):
    dns(monkeypatch, {(service, "SRV"): [srv(".", 0)]})

    with pytest.raises(OSError, match="unavailable"):
        SIPResolver().resolve(uri)


def test_unavailable_tcp_does_not_hide_an_available_udp_service(monkeypatch):
    dns(
        monkeypatch,
        {
            ("_sip._tcp.example.test", "SRV"): [srv(".", 0)],
            ("_sip._udp.example.test", "SRV"): [srv("edge.test.", 5070)],
        },
    )

    target = SIPResolver().resolve("sip:example.test")

    assert target.host == "edge.test"
    assert target.port == 5070
    assert target.transport is SIPTransport.UDP


def test_unavailable_udp_cannot_be_reintroduced_by_default_fallback(
    monkeypatch,
):
    dns(
        monkeypatch,
        {("_sip._udp.example.test", "SRV"): [srv(".", 0)]},
    )

    with pytest.raises(OSError, match="unavailable"):
        SIPResolver().resolve("sip:example.test")


def test_unavailable_tcp_still_allows_missing_udp_service_default(monkeypatch):
    dns(
        monkeypatch,
        {("_sip._tcp.example.test", "SRV"): [srv(".", 0)]},
    )

    target = SIPResolver().resolve("sip:example.test")

    assert target.host == "example.test"
    assert target.port == 5060
    assert target.transport is SIPTransport.UDP


def test_naptr_tries_next_service_after_explicitly_unavailable_target(
    monkeypatch,
):
    dns(
        monkeypatch,
        {
            ("example.test", "NAPTR"): [
                SimpleNamespace(
                    service="SIP+D2T",
                    flags="s",
                    order=1,
                    preference=0,
                    replacement="_sip._tcp.example.test.",
                ),
                SimpleNamespace(
                    service="SIP+D2U",
                    flags="s",
                    order=2,
                    preference=0,
                    replacement="_sip._udp.example.test.",
                ),
            ],
            ("_sip._tcp.example.test", "SRV"): [srv(".", 0)],
            ("_sip._udp.example.test", "SRV"): [srv("edge.test.", 5070)],
        },
    )

    target = SIPResolver().resolve("sip:example.test")

    assert target.host == "edge.test"
    assert target.transport is SIPTransport.UDP


def test_naptr_unavailable_services_cannot_fall_back_to_domain(monkeypatch):
    queries = dns(
        monkeypatch,
        {
            ("example.test", "NAPTR"): [
                SimpleNamespace(
                    service="SIP+D2T",
                    flags="s",
                    order=1,
                    preference=0,
                    replacement="_sip._tcp.example.test.",
                ),
            ],
            ("_sip._tcp.example.test", "SRV"): [srv(".", 0)],
        },
    )

    with pytest.raises(OSError, match="unavailable"):
        SIPResolver().resolve("sip:example.test")

    assert queries == [
        ("example.test", "NAPTR"),
        ("_sip._tcp.example.test", "SRV"),
    ]
