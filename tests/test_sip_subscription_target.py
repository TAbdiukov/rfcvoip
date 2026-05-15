from rfcvoip.SIP import SIPClient


class _Phone:
    _status = None


def test_subscription_target_inherits_sips_scheme_for_user_at_domain():
    client = SIPClient(
        "sips:127.0.0.1",
        None,
        "alice",
        "secret",
        phone=_Phone(),
        myIP="127.0.0.1",
    )

    assert client._normalize_subscription_target("bob@example.com").startswith(
        "sips:"
    )


def test_subscription_target_inherits_sips_scheme_for_extension():
    client = SIPClient(
        "sips:127.0.0.1",
        None,
        "alice",
        "secret",
        phone=_Phone(),
        myIP="127.0.0.1",
    )

    assert client._normalize_subscription_target("1001").startswith("sips:")