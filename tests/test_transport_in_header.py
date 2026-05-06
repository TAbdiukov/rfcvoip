from pyVoIP import RTP, SIP


class DummyPhone:
    _status = None
    calls = {}


def make_client(my_ip="192.0.2.10", my_port=5070):
    return SIP.SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP=my_ip,
        myPort=my_port,
    )


def test_contact_uri_advertises_udp_transport():
    client = make_client()

    assert (
        client._contact_uri()
        == "sip:alice@192.0.2.10:5070;transport=UDP"
    )


def test_contact_uri_brackets_ipv6_literals():
    client = make_client(my_ip="2001:db8::10")

    assert (
        client._contact_uri()
        == "sip:alice@[2001:db8::10]:5070;transport=UDP"
    )


def test_register_contact_advertises_udp_transport_and_instance():
    client = make_client()

    request = client.gen_first_response()

    assert (
        'Contact: <sip:alice@192.0.2.10:5070;transport=UDP>'
        ';+sip.instance="<urn:uuid:'
    ) in request


def test_subscribe_contact_advertises_udp_transport():
    client = make_client()
    subscription = SIP.SIPSubscription(
        call_id="call-id@example.test",
        target="bob",
        target_uri="sip:bob@example.com",
        event="presence",
        accept=["application/pidf+xml"],
        local_tag="localtag",
    )

    request = client._build_subscribe_request(subscription, expires=3600)

    assert (
        "Contact: <sip:alice@192.0.2.10:5070;transport=UDP>\r\n"
        in request
    )


def test_invite_contact_advertises_udp_transport():
    client = make_client()
    media = {
        10000: {
            0: RTP.PayloadType.PCMU,
            8: RTP.PayloadType.PCMA,
            101: RTP.PayloadType.EVENT,
        }
    }

    request = client.gen_invite(
        "bob",
        "1",
        media,
        RTP.TransmitType.SENDRECV,
        "z9hG4bKtestbranch",
        "call-id@example.test",
    )

    assert (
        "Contact: <sip:alice@192.0.2.10:5070;transport=UDP>\r\n"
        in request
    )
