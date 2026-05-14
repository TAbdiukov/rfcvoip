from pyVoIP.SIPAuth import (
    build_digest_auth_header,
    choose_digest_challenge,
    compute_digest_response,
    generate_nonce,
    make_digest_credential_hash,
    validate_nonce,
    verify_digest_response,
)


def test_rfc7616_md5_and_sha256_vectors():
    params = {
        "realm": "http-auth@example.org",
        "nonce": "7ypf/xlj9XXwfDPEoM4URrv/xwf94BcCAzFZH4GiTo0v",
        "qop": "auth",
        "uri": "/dir/index.html",
        "cnonce": "f2/wE4q74E6zIJEtWaHKaf5wv/H5QzzpXusqGemxURZJ",
        "nc": "00000001",
    }

    assert compute_digest_response(
        {**params, "algorithm": "MD5"},
        username="Mufasa",
        password="Circle of Life",
        method="GET",
    ) == "8ca523f5e9506fed4657c9700eebdbec"

    assert compute_digest_response(
        {**params, "algorithm": "SHA-256"},
        username="Mufasa",
        password="Circle of Life",
        method="GET",
    ) == (
        "753927fa0e85d155564e2e272a28d180"
        "2ca10daf4496794697cf8db5856cb6c1"
    )


def test_sha512_256_sess_can_verify_from_stored_ha1():
    auth = {
        "username": "alice",
        "realm": "sip.example.test",
        "nonce": "server-nonce",
        "uri": "sip:bob@example.test",
        "algorithm": "SHA-512-256-sess",
        "qop": "auth",
        "nc": "00000001",
        "cnonce": "client-nonce",
    }
    stored_ha1 = make_digest_credential_hash(
        "alice",
        "sip.example.test",
        "secret",
        "SHA-512-256",
    )
    auth["response"] = compute_digest_response(
        auth,
        stored_ha1=stored_ha1,
        method="INVITE",
    )

    assert len(auth["response"]) == 64
    assert verify_digest_response(
        auth,
        method="INVITE",
        stored_ha1=stored_ha1,
    )
    assert not verify_digest_response(
        auth,
        method="INVITE",
        stored_ha1="0" * 64,
    )


def test_choose_digest_challenge_prefers_strongest_supported_algorithm():
    selected = choose_digest_challenge(
        [
            {"realm": "r", "nonce": "n1", "algorithm": "MD5"},
            {"realm": "r", "nonce": "n2", "algorithm": "SHA-256"},
            {"realm": "r", "nonce": "n3", "algorithm": "SHA-512-256"},
        ]
    )

    assert selected is not None
    assert selected["algorithm"] == "SHA-512-256"
    assert selected["nonce"] == "n3"


def test_nonce_generation_and_validation():
    nonce = generate_nonce("server-secret", context="sip.example.test", now=1000)

    assert validate_nonce(
        nonce,
        "server-secret",
        context="sip.example.test",
        now=1001,
    )
    assert not validate_nonce(
        nonce,
        "server-secret",
        context="other",
        now=1001,
    )
    assert not validate_nonce(
        nonce,
        "server-secret",
        context="sip.example.test",
        now=2000,
    )


def test_build_digest_auth_header_uses_rfc7616_parameters():
    header = build_digest_auth_header(
        {"realm": "r", "nonce": "n", "algorithm": "SHA-256", "qop": "auth"},
        header_name="Authorization",
        username="alice",
        password="secret",
        method="REGISTER",
        uri="sip:example.test",
    )

    assert header.startswith("Authorization: Digest ")
    assert 'username="alice"' in header
    assert 'realm="r"' in header
    assert 'nonce="n"' in header
    assert 'uri="sip:example.test"' in header
    assert "algorithm=SHA-256" in header
    assert "qop=auth" in header
    assert "nc=00000001" in header
    assert 'cnonce="' in header