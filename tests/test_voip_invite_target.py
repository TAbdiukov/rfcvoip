from types import SimpleNamespace

from rfcvoip.VoIP.VoIP import VoIPPhone


def _phone(username="1001", auth_username=None):
    phone = VoIPPhone.__new__(VoIPPhone)
    phone.username = username
    phone.auth_username = username if auth_username is None else auth_username
    return phone


def _request(uri="", to_number=None, to_raw=None, to_address=None):
    return SimpleNamespace(
        uri=uri,
        headers={
            "To": {
                "number": to_number,
                "raw": to_raw or "",
                "address": to_address or "",
            }
        },
    )


def test_invite_target_accepts_matching_to_number():
    phone = _phone("1001")
    request = _request(to_number="1001")

    assert phone._invite_targets_local_account(request)


def test_invite_target_accepts_matching_request_uri_user():
    phone = _phone("1001")
    request = _request(uri="sip:1001@203.0.113.10:5060")

    assert phone._invite_targets_local_account(request)


def test_invite_target_rejects_different_to_user():
    phone = _phone("1001")
    request = _request(
        uri="sip:2002@203.0.113.10:5060",
        to_raw="<sip:2002@example.com>",
        to_address="2002@example.com",
        to_number="2002",
    )

    assert not phone._invite_targets_local_account(request)


def test_invite_target_accepts_auth_username_alias():
    phone = _phone("1001", auth_username="account-1001")
    request = _request(to_raw="<sip:account-1001@example.com>")

    assert phone._invite_targets_local_account(request)