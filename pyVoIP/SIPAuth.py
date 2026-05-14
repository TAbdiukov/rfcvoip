from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


__all__ = [
    "SUPPORTED_DIGEST_ALGORITHMS",
    "SIPAuthError",
    "build_digest_auth_header",
    "build_digest_challenge_headers",
    "choose_digest_challenge",
    "compute_digest_response",
    "generate_cnonce",
    "generate_nonce",
    "make_digest_credential_hash",
    "normalize_digest_algorithm",
    "parse_digest_params",
    "redact_sensitive_sip_headers",
    "validate_nonce",
    "verify_digest_response",
]


SUPPORTED_DIGEST_ALGORITHMS = (
    "SHA-512-256",
    "SHA-512-256-sess",
    "SHA-256",
    "SHA-256-sess",
    "MD5-sess",
    "MD5",
)

_HASHLIB_ALGORITHMS = {
    "MD5": "md5",
    "SHA-256": "sha256",
    "SHA-512-256": "sha512_256",
}

_ALGORITHM_ALIASES = {
    "MD5": "MD5",
    "MD5-SESS": "MD5-sess",
    "SHA-256": "SHA-256",
    "SHA256": "SHA-256",
    "SHA-256-SESS": "SHA-256-sess",
    "SHA256-SESS": "SHA-256-sess",
    "SHA-512-256": "SHA-512-256",
    "SHA512-256": "SHA-512-256",
    "SHA-512/256": "SHA-512-256",
    "SHA512/256": "SHA-512-256",
    "SHA512_256": "SHA-512-256",
    "SHA-512-256-SESS": "SHA-512-256-sess",
    "SHA512-256-SESS": "SHA-512-256-sess",
    "SHA-512/256-SESS": "SHA-512-256-sess",
    "SHA512/256-SESS": "SHA-512-256-sess",
    "SHA512_256-SESS": "SHA-512-256-sess",
}

_ALGORITHM_PREFERENCE = {
    algorithm: index for index, algorithm in enumerate(SUPPORTED_DIGEST_ALGORITHMS)
}


class SIPAuthError(Exception):
    pass


def parse_digest_params(value: str) -> Dict[str, str]:
    data = str(value or "").strip()
    if data.lower().startswith("digest"):
        data = data[len("Digest") :].lstrip()

    params: Dict[str, str] = {}
    index = 0
    length = len(data)

    while index < length:
        while index < length and data[index] in " \t\r\n,":
            index += 1
        if index >= length:
            break

        key_start = index
        while index < length and data[index] not in "=, \t\r\n":
            index += 1
        key = data[key_start:index].strip()

        while index < length and data[index].isspace():
            index += 1
        if index >= length or data[index] != "=":
            while index < length and data[index] != ",":
                index += 1
            continue

        index += 1
        while index < length and data[index].isspace():
            index += 1

        if index < length and data[index] == '"':
            index += 1
            chars = []
            while index < length:
                char = data[index]
                if char == "\\" and index + 1 < length:
                    chars.append(data[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                chars.append(char)
                index += 1
            parsed_value = "".join(chars)
        else:
            value_start = index
            while index < length and data[index] != ",":
                index += 1
            parsed_value = data[value_start:index].strip()

        if key:
            params[key.lower()] = parsed_value

        while index < length and data[index] != ",":
            index += 1
        if index < length and data[index] == ",":
            index += 1

    return params


def redact_sensitive_sip_headers(message: str) -> str:
    redacted = []
    for line in str(message or "").splitlines():
        header = line.split(":", 1)[0].strip().lower()
        if header in ("authorization", "proxy-authorization"):
            redacted.append(line.split(":", 1)[0] + ": <redacted>")
        else:
            redacted.append(line)
    return "\n".join(redacted)


def normalize_digest_algorithm(algorithm: Optional[str]) -> str:
    raw = str(algorithm or "MD5").strip().strip('"')
    if not raw:
        raw = "MD5"
    return _ALGORITHM_ALIASES.get(raw.upper(), raw)


def _base_algorithm(algorithm: Optional[str]) -> str:
    algorithm = normalize_digest_algorithm(algorithm)
    if algorithm.endswith("-sess"):
        return algorithm[: -len("-sess")]
    return algorithm


def _is_session_algorithm(algorithm: Optional[str]) -> bool:
    return normalize_digest_algorithm(algorithm).endswith("-sess")


def _ensure_supported_algorithm(
    algorithm: Optional[str],
    supported_algorithms: Optional[Iterable[str]] = None,
) -> str:
    algorithm = normalize_digest_algorithm(algorithm)
    supported = {
        normalize_digest_algorithm(item)
        for item in (supported_algorithms or SUPPORTED_DIGEST_ALGORITHMS)
    }
    if algorithm not in supported:
        raise SIPAuthError(f"Unsupported SIP digest algorithm {algorithm!r}.")
    if _base_algorithm(algorithm) not in _HASHLIB_ALGORITHMS:
        raise SIPAuthError(f"Unsupported SIP digest algorithm {algorithm!r}.")
    return algorithm


def _hash_bytes(data: bytes, algorithm: Optional[str]) -> str:
    base_algorithm = _base_algorithm(algorithm)
    hashlib_name = _HASHLIB_ALGORITHMS.get(base_algorithm)
    if hashlib_name is None:
        raise SIPAuthError(f"Unsupported SIP digest algorithm {algorithm!r}.")
    try:
        digest = hashlib.new(hashlib_name)
    except ValueError as ex:
        raise SIPAuthError(
            f"hashlib does not provide {hashlib_name!r}; "
            + f"cannot compute {base_algorithm} SIP digest."
        ) from ex
    digest.update(data)
    return digest.hexdigest()


def _hash_text(text: str, algorithm: Optional[str]) -> str:
    return _hash_bytes(str(text).encode("utf-8"), algorithm)


def make_digest_credential_hash(
    username: str,
    realm: str,
    password: str,
    algorithm: str = "SHA-256",
) -> str:
    """Return H(username:realm:password) for credential storage.

    For a ``-sess`` algorithm, store the hash for the base algorithm, for
    example store ``SHA-256`` H(A1) for both ``SHA-256`` and
    ``SHA-256-sess``.
    """
    algorithm = _ensure_supported_algorithm(algorithm)
    return _hash_text(f"{username}:{realm}:{password}", algorithm)


def _qop_tokens(qop: Optional[str]) -> List[str]:
    if qop is None:
        return []
    return [
        token.strip().lower()
        for token in str(qop).strip().strip('"').split(",")
        if token.strip()
    ]


def _select_qop(qop: Optional[str]) -> Optional[str]:
    tokens = _qop_tokens(qop)
    if not tokens:
        return None
    if "auth" in tokens:
        return "auth"
    if "auth-int" in tokens:
        return "auth-int"
    raise SIPAuthError(f"Unsupported SIP digest qop {qop!r}.")


def _normalize_authorization_qop(qop: Optional[str]) -> Optional[str]:
    if qop in (None, ""):
        return None
    qop_value = str(qop).strip().strip('"').lower()
    if qop_value not in ("auth", "auth-int"):
        raise SIPAuthError(f"Unsupported SIP digest qop {qop!r}.")
    return qop_value


def generate_cnonce(bytes_of_entropy: int = 18) -> str:
    return secrets.token_urlsafe(bytes_of_entropy)


def _nonce_secret_bytes(secret: Union[str, bytes]) -> bytes:
    if isinstance(secret, bytes):
        return secret
    return str(secret).encode("utf-8")


def generate_nonce(
    secret: Union[str, bytes],
    *,
    context: str = "",
    now: Optional[float] = None,
) -> str:
    """Generate an opaque, timestamped, HMAC-protected nonce for SIP digest."""
    issued_at = int(time.time() if now is None else now)
    payload = {
        "iat": issued_at,
        "rnd": secrets.token_urlsafe(18),
        "ctx": str(context or ""),
    }
    payload_bytes = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload_token = (
        base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    )
    signature = hmac.new(
        _nonce_secret_bytes(secret),
        payload_token.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_token}.{signature}"


def validate_nonce(
    nonce: str,
    secret: Union[str, bytes],
    *,
    context: str = "",
    max_age_seconds: int = 300,
    now: Optional[float] = None,
) -> bool:
    try:
        payload_token, signature = str(nonce or "").split(".", 1)
    except ValueError:
        return False

    expected = hmac.new(
        _nonce_secret_bytes(secret),
        payload_token.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False

    padding = "=" * (-len(payload_token) % 4)
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(payload_token + padding).decode("utf-8")
        )
    except Exception:
        return False

    if str(payload.get("ctx", "")) != str(context or ""):
        return False

    try:
        issued_at = int(payload["iat"])
    except Exception:
        return False

    current_time = int(time.time() if now is None else now)
    if issued_at > current_time + 5:
        return False
    return current_time - issued_at <= int(max_age_seconds)


def _ha1_digest(
    *,
    username: str,
    realm: str,
    password: Optional[str],
    algorithm: str,
    stored_ha1: Optional[str] = None,
) -> str:
    if stored_ha1 is not None:
        return str(stored_ha1).strip().lower()
    if password is None:
        raise SIPAuthError("A password or stored H(A1) is required.")
    return make_digest_credential_hash(
        username,
        realm,
        password,
        algorithm,
    )


def compute_digest_response(
    challenge_or_authorization: Dict[str, Any],
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    stored_ha1: Optional[str] = None,
    method: str,
    uri: Optional[str] = None,
    nonce: Optional[str] = None,
    cnonce: Optional[str] = None,
    nonce_count: Optional[str] = None,
    qop: Optional[str] = None,
    body: bytes = b"",
    supported_algorithms: Optional[Iterable[str]] = None,
) -> str:
    params = challenge_or_authorization or {}
    algorithm = _ensure_supported_algorithm(
        params.get("algorithm"),
        supported_algorithms,
    )
    realm = str(params.get("realm") or "")
    nonce_value = str(nonce or params.get("nonce") or "")
    uri_value = str(uri or params.get("uri") or "")
    username_value = str(username or params.get("username") or "")

    if not realm:
        raise SIPAuthError("Digest challenge is missing realm.")
    if not nonce_value:
        raise SIPAuthError("Digest challenge is missing nonce.")
    if not uri_value:
        raise SIPAuthError("Digest calculation requires a URI.")
    if not username_value:
        raise SIPAuthError("Digest calculation requires a username.")

    if qop is None:
        qop_value = _select_qop(params.get("qop"))
    else:
        qop_value = _normalize_authorization_qop(qop)

    cnonce_value = str(cnonce or params.get("cnonce") or "")
    nc_value = str(nonce_count or params.get("nc") or "00000001")
    if qop_value is not None and not cnonce_value:
        raise SIPAuthError("Digest qop requires cnonce.")
    if _is_session_algorithm(algorithm) and not cnonce_value:
        raise SIPAuthError("Digest session algorithms require cnonce.")

    ha1 = _ha1_digest(
        username=username_value,
        realm=realm,
        password=password,
        stored_ha1=stored_ha1,
        algorithm=algorithm,
    )
    if _is_session_algorithm(algorithm):
        ha1 = _hash_text(f"{ha1}:{nonce_value}:{cnonce_value}", algorithm)

    if qop_value == "auth-int":
        entity_hash = _hash_bytes(body or b"", algorithm)
        a2 = f"{method}:{uri_value}:{entity_hash}"
    else:
        a2 = f"{method}:{uri_value}"
    ha2 = _hash_text(a2, algorithm)

    if qop_value is None:
        response_input = f"{ha1}:{nonce_value}:{ha2}"
    else:
        response_input = (
            f"{ha1}:{nonce_value}:{nc_value}:{cnonce_value}:"
            + f"{qop_value}:{ha2}"
        )
    return _hash_text(response_input, algorithm)


def _ordered_algorithms(algorithms: Iterable[str]) -> List[str]:
    normalized = []
    for algorithm in algorithms:
        try:
            normalized.append(_ensure_supported_algorithm(algorithm))
        except SIPAuthError:
            continue

    return sorted(
        dict.fromkeys(normalized),
        key=lambda item: _ALGORITHM_PREFERENCE.get(item, 999),
    )


def choose_digest_challenge(
    challenges: Sequence[Dict[str, Any]],
    *,
    supported_algorithms: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, str]]:
    supported = _ordered_algorithms(
        supported_algorithms or SUPPORTED_DIGEST_ALGORITHMS
    )
    if not supported:
        return None
    preference = {algorithm: index for index, algorithm in enumerate(supported)}

    candidates: List[Tuple[int, int, Dict[str, str]]] = []
    for index, challenge in enumerate(challenges):
        if not isinstance(challenge, dict):
            continue
        try:
            algorithm = _ensure_supported_algorithm(
                challenge.get("algorithm"),
                supported,
            )
        except SIPAuthError:
            continue
        candidates.append(
            (
                preference.get(algorithm, 999),
                index,
                {
                    str(key): str(value)
                    for key, value in challenge.items()
                    if value is not None
                },
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _quote(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_authorization_parameters(
    challenge: Dict[str, Any],
    *,
    username: str,
    password: str,
    method: str,
    uri: str,
    nonce_count: str = "00000001",
    body: bytes = b"",
    supported_algorithms: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    algorithm = _ensure_supported_algorithm(
        challenge.get("algorithm"),
        supported_algorithms,
    )
    qop_value = _select_qop(challenge.get("qop"))
    cnonce_value = (
        generate_cnonce()
        if qop_value is not None or _is_session_algorithm(algorithm)
        else ""
    )
    params: Dict[str, str] = {
        "username": username,
        "realm": str(challenge.get("realm") or ""),
        "nonce": str(challenge.get("nonce") or ""),
        "uri": uri,
        "algorithm": algorithm,
    }
    if cnonce_value:
        params["cnonce"] = cnonce_value
    if qop_value is not None:
        params["qop"] = qop_value
        params["nc"] = nonce_count

    params["response"] = compute_digest_response(
        params,
        username=username,
        password=password,
        method=method,
        uri=uri,
        nonce=params["nonce"],
        cnonce=params.get("cnonce"),
        nonce_count=params.get("nc"),
        qop=params.get("qop"),
        body=body,
        supported_algorithms=supported_algorithms,
    )

    opaque = challenge.get("opaque")
    if opaque:
        params["opaque"] = str(opaque)
    return params


def build_digest_auth_header(
    challenge: Dict[str, Any],
    *,
    header_name: str,
    username: str,
    password: str,
    method: str,
    uri: str,
    nonce_count: str = "00000001",
    body: bytes = b"",
    supported_algorithms: Optional[Iterable[str]] = None,
) -> str:
    params = build_authorization_parameters(
        challenge,
        username=username,
        password=password,
        method=method,
        uri=uri,
        nonce_count=nonce_count,
        body=body,
        supported_algorithms=supported_algorithms,
    )

    fields = [
        f'username="{_quote(params["username"])}"',
        f'realm="{_quote(params["realm"])}"',
        f'nonce="{_quote(params["nonce"])}"',
        f'uri="{_quote(params["uri"])}"',
        f'response="{_quote(params["response"])}"',
        f'algorithm={params["algorithm"]}',
    ]
    if "qop" in params:
        fields.extend(
            [
                f'qop={params["qop"]}',
                f'nc={params["nc"]}',
                f'cnonce="{_quote(params["cnonce"])}"',
            ]
        )
    elif "cnonce" in params:
        fields.append(f'cnonce="{_quote(params["cnonce"])}"')
    if "opaque" in params:
        fields.append(f'opaque="{_quote(params["opaque"])}"')

    return f"{header_name}: Digest " + ",".join(fields) + "\r\n"


def verify_digest_response(
    authorization: Dict[str, Any],
    *,
    method: str,
    password: Optional[str] = None,
    stored_ha1: Optional[str] = None,
    body: bytes = b"",
    supported_algorithms: Optional[Iterable[str]] = None,
) -> bool:
    if not isinstance(authorization, dict):
        return False

    actual = str(authorization.get("response") or "").strip().strip('"')
    if not actual:
        return False

    try:
        expected = compute_digest_response(
            authorization,
            password=password,
            stored_ha1=stored_ha1,
            method=method,
            body=body,
            supported_algorithms=supported_algorithms,
        )
    except SIPAuthError:
        return False

    return hmac.compare_digest(expected.lower(), actual.lower())


def build_digest_challenge_headers(
    realm: str,
    *,
    nonce_secret: Union[str, bytes],
    header_name: str = "WWW-Authenticate",
    algorithms: Iterable[str] = SUPPORTED_DIGEST_ALGORITHMS,
    qop: Iterable[str] = ("auth",),
    opaque: Optional[str] = None,
    context: Optional[str] = None,
) -> List[str]:
    challenge_algorithms = _ordered_algorithms(algorithms)
    nonce = generate_nonce(nonce_secret, context=context or realm)
    qop_value = ", ".join(token for token in qop if token)

    headers = []
    for algorithm in challenge_algorithms:
        fields = [
            f'realm="{_quote(realm)}"',
            f'nonce="{_quote(nonce)}"',
            f"algorithm={algorithm}",
        ]
        if qop_value:
            fields.append(f'qop="{_quote(qop_value)}"')
        if opaque is not None:
            fields.append(f'opaque="{_quote(opaque)}"')
        headers.append(f"{header_name}: Digest " + ",".join(fields) + "\r\n")
    return headers