import pytest

from rfcvoip.SIPAuth import (
    SIPAuthError,
    build_digest_auth_header,
    build_digest_challenge_headers,
    choose_digest_challenge,
    generate_nonce,
    make_digest_credential_hash,
    normalize_digest_algorithm,
    parse_digest_params,
    redact_sensitive_sip_headers,
    validate_nonce,
    verify_digest_response,
)


def _digest_params_from_header(header_line):
    return parse_digest_params(header_line.split(":", 1)[1].strip())


def test_digest_challenge_response_round_trips_with_strongest_algorithm():
    headers = build_digest_challenge_headers(
        "example.org",
        nonce_secret="nonce-secret",
        algorithms=("MD5", "SHA-256"),
        qop=("auth",),
        opaque="opaque-token",
        context="register",
    )
    challenges = [_digest_params_from_header(header) for header in headers]

    # Reverse the generated order to prove selection follows strength, not
    # input order.
    challenge = choose_digest_challenge(list(reversed(challenges)))

    assert challenge["algorithm"] == "SHA-256"

    auth_header = build_digest_auth_header(
        challenge,
        header_name="Authorization",
        username="alice",
        password="correct horse battery staple",
        method="REGISTER",
        uri="sip:example.org",
    )
    auth = _digest_params_from_header(auth_header)

    assert auth_header.startswith("Authorization: Digest ")
    assert auth["algorithm"] == "SHA-256"
    assert auth["qop"] == "auth"
    assert auth["opaque"] == "opaque-token"
    assert verify_digest_response(
        auth,
        method="REGISTER",
        password="correct horse battery staple",
    )
    assert not verify_digest_response(
        auth,
        method="REGISTER",
        password="wrong password",
    )


def test_auth_int_session_digest_binds_response_to_request_body():
    body = b"v=0\r\ns=rfcvoip\r\n"
    challenge = {
        "realm": "example.org",
        "nonce": "nonce-1",
        "algorithm": "SHA-256-sess",
        "qop": "auth-int",
    }

    auth_header = build_digest_auth_header(
        challenge,
        header_name="Proxy-Authorization",
        username="alice",
        password="secret",
        method="INVITE",
        uri="sip:bob@example.org",
        body=body,
    )
    auth = _digest_params_from_header(auth_header)

    assert auth_header.startswith("Proxy-Authorization: Digest ")
    assert auth["algorithm"] == "SHA-256-sess"
    assert auth["qop"] == "auth-int"
    assert auth["cnonce"]
    assert verify_digest_response(
        auth,
        method="INVITE",
        password="secret",
        body=body,
    )
    assert not verify_digest_response(
        auth,
        method="INVITE",
        password="secret",
        body=b"v=0\r\ns=tampered\r\n",
    )


def test_stored_ha1_verifies_digest_without_plaintext_password():
    username = "alice"
    realm = "example.org"
    password = "secret"
    challenge = {
        "realm": realm,
        "nonce": "nonce-1",
        "algorithm": "SHA-256",
        "qop": "auth",
    }
    auth_header = build_digest_auth_header(
        challenge,
        header_name="Authorization",
        username=username,
        password=password,
        method="OPTIONS",
        uri="sip:service@example.org",
    )
    auth = _digest_params_from_header(auth_header)
    stored_ha1 = make_digest_credential_hash(
        username,
        realm,
        password,
        "SHA-256",
    )
    wrong_ha1 = make_digest_credential_hash(
        username,
        realm,
        "wrong password",
        "SHA-256",
    )

    assert verify_digest_response(
        auth,
        method="OPTIONS",
        stored_ha1=stored_ha1,
    )
    assert not verify_digest_response(
        auth,
        method="OPTIONS",
        stored_ha1=wrong_ha1,
    )


def test_nonce_validation_rejects_wrong_context_secret_tamper_and_expiry():
    nonce = generate_nonce("nonce-secret", context="register", now=1000)
    tampered_nonce = nonce[:-1] + ("0" if nonce[-1] != "0" else "1")

    assert validate_nonce(
        nonce,
        "nonce-secret",
        context="register",
        now=1005,
        max_age_seconds=10,
    )
    assert not validate_nonce(
        nonce,
        "nonce-secret",
        context="invite",
        now=1005,
        max_age_seconds=10,
    )
    assert not validate_nonce(
        nonce,
        "wrong-secret",
        context="register",
        now=1005,
        max_age_seconds=10,
    )
    assert not validate_nonce(
        tampered_nonce,
        "nonce-secret",
        context="register",
        now=1005,
        max_age_seconds=10,
    )
    assert not validate_nonce(
        nonce,
        "nonce-secret",
        context="register",
        now=1011,
        max_age_seconds=10,
    )


def test_parse_digest_params_handles_quoted_commas_and_escaped_quotes():
    params = parse_digest_params(
        r'Digest realm="example, realm", nonce="abc\"def", '
        r'qop="auth,auth-int", algorithm=SHA512_256, '
        r'opaque="opaque,value"'
    )

    assert params["realm"] == "example, realm"
    assert params["nonce"] == 'abc"def'
    assert params["qop"] == "auth,auth-int"
    assert params["opaque"] == "opaque,value"
    assert normalize_digest_algorithm(params["algorithm"]) == "SHA-512-256"


def test_redact_sensitive_sip_headers_redacts_auth_headers_and_folding():
    message = (
        "SIP/2.0 401 Unauthorized\r\n"
        'Authorization: Digest username="alice",response="secret"\r\n'
        ' nonce="still-secret"\r\n'
        'Proxy-Authorization: Digest username="alice",response="proxy-secret"\r\n'
        "Via: SIP/2.0/UDP example.org\r\n"
    )

    redacted = redact_sensitive_sip_headers(message)

    assert redacted.splitlines() == [
        "SIP/2.0 401 Unauthorized",
        "Authorization: <redacted>",
        " <redacted>",
        "Proxy-Authorization: <redacted>",
        "Via: SIP/2.0/UDP example.org",
    ]
    assert "alice" not in redacted
    assert "secret" not in redacted


@pytest.mark.parametrize(
    ("challenge", "error"),
    [
        (
            {
                "realm": "example.org",
                "nonce": "nonce-1",
                "algorithm": "SHA-1",
            },
            "Unsupported SIP digest algorithm",
        ),
        (
            {
                "realm": "example.org",
                "nonce": "nonce-1",
                "algorithm": "SHA-256",
                "qop": "auth-conf",
            },
            "Unsupported SIP digest qop",
        ),
    ],
)
def test_unsupported_digest_challenges_are_rejected(challenge, error):
    with pytest.raises(SIPAuthError, match=error):
        build_digest_auth_header(
            challenge,
            header_name="Authorization",
            username="alice",
            password="secret",
            method="REGISTER",
            uri="sip:example.org",
        )