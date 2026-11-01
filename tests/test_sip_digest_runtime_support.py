import pytest

import rfcvoip.SIPAuth as auth


@pytest.fixture
def missing_sha512_256(monkeypatch):
    new = auth.hashlib.new

    def create(name, *args, **kwargs):
        if name == "sha512_256":
            raise ValueError("Hash unavailable in this Python build")
        return new(name, *args, **kwargs)

    monkeypatch.setattr(auth.hashlib, "new", create)


@pytest.mark.parametrize("suffix", ["", "-sess"])
def test_challenge_selection_skips_missing_runtime_hash(
    missing_sha512_256, suffix
):
    challenges = [
        {"realm": "r", "nonce": "n", "algorithm": algorithm + suffix}
        for algorithm in ("SHA-512-256", "SHA-256")
    ]

    selected = auth.choose_digest_challenge(challenges)
    header = auth.build_digest_auth_header(
        selected,
        header_name="Authorization",
        username="alice",
        password="secret",
        method="REGISTER",
        uri="sip:example.test",
    )

    assert selected["algorithm"] == "SHA-256" + suffix
    params = auth.parse_digest_params(header.split(":", 1)[1])
    assert auth.verify_digest_response(
        params, method="REGISTER", password="secret"
    )


def test_unavailable_hash_cannot_override_explicit_algorithm_policy(
    missing_sha512_256,
):
    selected = auth.choose_digest_challenge(
        [
            {"realm": "r", "nonce": "n", "algorithm": "SHA-512-256"},
            {"realm": "r", "nonce": "n", "algorithm": "MD5"},
        ],
        supported_algorithms=("SHA-512-256",),
    )

    assert selected is None


def test_generated_challenges_only_advertise_runtime_supported_hashes(
    missing_sha512_256,
):
    headers = auth.build_digest_challenge_headers("r", nonce_secret="secret")
    algorithms = {
        auth.parse_digest_params(header.split(":", 1)[1])["algorithm"]
        for header in headers
    }

    assert algorithms == {"SHA-256", "SHA-256-sess", "MD5", "MD5-sess"}
    assert (
        auth.build_digest_challenge_headers(
            "r", nonce_secret="secret", algorithms=("SHA-512-256",)
        )
        == []
    )


@pytest.mark.parametrize("opaque", ["", "0", 'quoted"value'])
def test_authorization_echoes_every_supplied_opaque_value(opaque):
    header = auth.build_digest_auth_header(
        {"realm": "r", "nonce": "n", "opaque": opaque},
        header_name="Authorization",
        username="alice",
        password="secret",
        method="REGISTER",
        uri="sip:example.test",
    )

    params = auth.parse_digest_params(header.split(":", 1)[1])
    assert params["opaque"] == opaque
    assert auth.verify_digest_response(
        params, method="REGISTER", password="secret"
    )
