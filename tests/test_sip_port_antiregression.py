from rfcvoip.SIP import SIPClient, SIPMessage


class DummyPhone:
    pass


def _client(my_ip="192.0.2.10", my_port=5060):
    return SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP=my_ip,
        myPort=my_port,
    )


def _contact_lines(message):
    return [
        line
        for line in message.split("\r\n")
        if line.startswith("Contact:")
    ]


def test_contact_uri_keeps_default_port_and_udp_transport():
    client = _client(my_port=5060)

    assert (
        client._contact_uri()
        == "sip:alice@192.0.2.10:5060;transport=UDP"
    )
    assert (
        client._contact_header()
        == "Contact: <sip:alice@192.0.2.10:5060;transport=UDP>\r\n"
    )


def test_contact_uri_keeps_explicit_ipv6_default_port_and_udp_transport():
    client = _client(my_ip="2001:db8::10", my_port=5060)

    assert (
        client._contact_uri()
        == "sip:alice@[2001:db8::10]:5060;transport=UDP"
    )


def test_register_refresh_and_deregister_use_same_contact_binding():
    client = _client(my_port=5060)

    challenge = SIPMessage(
        b"SIP/2.0 401 Unauthorized\r\n"
        b"Via: SIP/2.0/UDP 192.0.2.10:5060;branch=z9hG4bKtest;rport\r\n"
        b"From: <sip:alice@sip.example.com>;tag=local\r\n"
        b"To: <sip:alice@sip.example.com>\r\n"
        b"Call-ID: test-call-id\r\n"
        b"CSeq: 1 REGISTER\r\n"
        b'WWW-Authenticate: Digest realm="sip.example.com",nonce="abc123"\r\n'
        b"Content-Length: 0\r\n\r\n"
    )

    initial_register = client.gen_first_response()
    refresh_register = client.gen_register(challenge)
    deregister = client.gen_first_response(deregister=True)

    expected = (
        'Contact: <sip:alice@192.0.2.10:5060;transport=UDP>'
        f';+sip.instance="<urn:uuid:{client.urnUUID}>"'
    )

    assert _contact_lines(initial_register) == [expected]
    assert _contact_lines(refresh_register) == [expected]
    assert _contact_lines(deregister) == [expected]


def test_contact_uri_keeps_explicit_default_port2():
    client = SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP="192.0.2.10",
        myPort=5060,
    )

    assert client._contact_uri() == "sip:alice@192.0.2.10:5060;transport=UDP"
    assert client._contact_header() == (
        "Contact: <sip:alice@192.0.2.10:5060;transport=UDP>\r\n"
    )

def test_register_and_deregister_use_same_contact_binding2():
    client = SIPClient(
        "sip.example.com",
        5060,
        "alice",
        "secret",
        phone=DummyPhone(),
        myIP="192.0.2.10",
        myPort=5060,
    )

    register = client.gen_first_response(deregister=False)
    deregister = client.gen_first_response(deregister=True)

    expected = (
        'Contact: <sip:alice@192.0.2.10:5060;transport=UDP>'
        f';+sip.instance="<urn:uuid:{client.urnUUID}>"\r\n'
    )

    assert expected in register
    assert expected in deregister

