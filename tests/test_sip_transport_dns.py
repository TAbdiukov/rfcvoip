import pyVoIP.SIPTransport as sip_transport
from pyVoIP.SIPTransport import SIPResolver, SIPTransport


class _FailingDNS:
    def __init__(self):
        self.queries = []

    def resolve(self, name, rdtype):
        self.queries.append((name, rdtype))
        raise RuntimeError("no test DNS records")


def test_plain_sip_srv_fallback_does_not_query_sips(monkeypatch):
    dns = _FailingDNS()
    monkeypatch.setattr(sip_transport, "dns_resolver", dns)

    resolver = SIPResolver(
        transport_preference=(
            SIPTransport.TLS,
            SIPTransport.TCP,
            SIPTransport.UDP,
        )
    )

    resolver.resolve("sip:example.com", default_port=None)

    assert ("_sips._tcp.example.com", "SRV") not in dns.queries
    assert ("_sip._tcp.example.com", "SRV") in dns.queries
    assert ("_sip._udp.example.com", "SRV") in dns.queries


def test_explicit_tls_transport_can_query_sips_srv(monkeypatch):
    dns = _FailingDNS()
    monkeypatch.setattr(sip_transport, "dns_resolver", dns)

    resolver = SIPResolver()

    resolver.resolve("sip:example.com;transport=tls", default_port=None)

    assert ("_sips._tcp.example.com", "SRV") in dns.queries