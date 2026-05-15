from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
import re
import time


__all__ = [
    "active_codecs",
    "authentication_info",
    "available_codecs",
    "call_codec_report",
    "codec_availability",
    "codec_info",
    "codec_support_report",
    "get",
    "local_codec_offer",
    "phone_codec_report",
    "report",
    "remote_supported_codecs",
    "sip_message_codec_report",
    "sip_supported_codecs",
    "snapshot",
    "supported_codecs",
]


_MISSING = object()


_PATH_TOKEN_RE = re.compile(
    r"""
    (?P<name>[^.\[\]]+)
    |
    \[(?P<bracket>(?:[^\]"']+|"(?:\\.|[^"])*"|'(?:\\.|[^'])*')+)\]
    """,
    re.VERBOSE,
)


_MARKDOWN_SPECIALS_V2 = "\\_*[]()~`>#+-=|{}.!"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _protocol_value(protocol: Any) -> str:
    return str(getattr(protocol, "value", protocol))


def _bandwidths_to_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _codec_required_bandwidth_bps(codec: Any) -> Optional[int]:
    try:
        from pyVoIP.codecs import codec_required_bandwidth_bps as required_bps

        return required_bps(codec)
    except Exception:
        return None


def _bandwidth_context(
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
    codec: Any = None,
) -> Dict[str, Any]:
    try:
        from pyVoIP import SIP

        limit = SIP._enforceable_bandwidth_limit_bps(
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        )
    except Exception:
        limit = None

    return {
        "session": _bandwidths_to_list(session_bandwidth),
        "media": _bandwidths_to_list(media_bandwidth),
        "limit_bps": limit,
        "required_bps": _codec_required_bandwidth_bps(codec),
    }


def _media_protocol_supported(media: Dict[str, Any]) -> bool:
    from pyVoIP import RTP

    protocol = media.get("protocol")
    return protocol in (RTP.RTPProtocol.AVP, RTP.RTPProtocol.AVP.value, "RTP/AVP")


def _fmtp_settings(attributes: Dict[str, Any]) -> List[str]:
    fmtp = attributes.get("fmtp", {})
    if isinstance(fmtp, dict):
        settings = fmtp.get("settings", [])
        return [str(setting) for setting in settings]
    return []


def _codec_availability_details(codec: Any) -> Dict[str, Any]:
    from pyVoIP import RTP

    try:
        from pyVoIP.codecs import codec_availability as _availability

        return _availability(codec)
    except Exception as ex:
        return {
            "available": False,
            "reason": str(ex),
            "library": None,
            "name": str(codec),
            "description": getattr(codec, "description", None),
            "payload_kind": RTP.payload_type_media_kind(codec),
            "rate": getattr(codec, "rate", 0),
            "channels": getattr(codec, "channel", 0),
            "can_transmit_audio": False,
            "default_payload_type": None,
            "is_dynamic": True,
            "priority_score": 0,
            "preferred_source_sample_rate": None,
            "required_bandwidth_bps": None,
        }


def codec_availability(
    codec: Optional[Any] = None,
    *,
    refresh: bool = False,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Return codec availability telemetry.

    ``codec`` may be a :class:`pyVoIP.RTP.PayloadType`, codec name, or payload
    number. With no codec this returns every known codec implementation,
    including optional codecs that are not currently available.
    """
    if codec is not None:
        try:
            from pyVoIP.codecs import _normalize_payload_type

            codec = _normalize_payload_type(codec)
        except Exception:
            pass
        return _codec_availability_details(codec)

    from pyVoIP.codecs import availability_report

    return availability_report(refresh=refresh)


def codec_info(
    codec: Any,
    payload_type: Optional[int] = None,
    *,
    media_type: Optional[str] = None,
    fmtp: Optional[List[str]] = None,
    source: str = "pyvoip",
    supported: Optional[bool] = None,
    priority_scores: Optional[Dict[Any, int]] = None,
    enabled_codecs: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return a serializable RTP codec telemetry record."""
    import pyVoIP
    from pyVoIP import RTP

    availability = _codec_availability_details(codec)
    preferred_payload_type = RTP.default_payload_type(codec)
    fmtp_list = list(fmtp or [])
    fmtp_supported = RTP.codec_fmtp_supported(codec, fmtp_list)
    compatible_codecs = (
        getattr(pyVoIP, "RTPCompatibleCodecs", [])
        if enabled_codecs is None
        else enabled_codecs
    )

    if payload_type is None:
        payload_type = preferred_payload_type

    if payload_type is None:
        try:
            payload_type = int(codec)
        except Exception:
            payload_type = None

    if supported is None:
        supported = (
            codec in compatible_codecs
            and bool(availability.get("available", True))
            and fmtp_supported
        )

    is_dynamic = (
        payload_type is None
        or not isinstance(getattr(codec, "value", None), int)
        or (isinstance(payload_type, int) and payload_type >= 96)
    )

    return {
        "media_type": media_type,
        "payload_type": payload_type,
        "name": str(codec),
        "description": getattr(codec, "description", None),
        "payload_kind": RTP.payload_type_media_kind(codec),
        "can_transmit_audio": RTP.is_transmittable_audio_codec(
            codec,
            enabled_codecs=compatible_codecs,
        ),
        "priority_score": RTP.codec_priority_score(
            codec,
            priority_scores=priority_scores,
        ),
        "rate": getattr(codec, "rate", None),
        "channels": getattr(codec, "channel", None),
        "preferred_source_sample_rate": availability.get(
            "preferred_source_sample_rate"
        ),
        "is_dynamic": is_dynamic,
        "fmtp": fmtp_list,
        "fmtp_supported": fmtp_supported,
        "codec_supported": bool(supported),
        "protocol_supported": None,
        "supported": bool(supported),
        "available": bool(availability.get("available", supported)),
        "availability_reason": availability.get("reason"),
        "library": availability.get("library"),
        "default_payload_type": preferred_payload_type,
        "rtpmap": (
            RTP.rtpmap_for_payload_type(payload_type, codec)
            if payload_type is not None
            else None
        ),
        "source": source,
    }


def _prioritize_payload_types(
    payload_types: List[Any],
    priority_scores: Optional[Dict[Any, int]] = None,
) -> List[Any]:
    from pyVoIP import RTP

    indexed = list(enumerate(payload_types))
    indexed.sort(
        key=lambda item: (
            -RTP.codec_priority_score(item[1], priority_scores=priority_scores),
            item[0],
        )
    )
    return [payload_type for _idx, payload_type in indexed]


def supported_codecs(
    include_unavailable: bool = False,
    priority_scores: Optional[Dict[Any, int]] = None,
    enabled_codecs: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return local codec telemetry for this PyVoIP build/configuration."""
    import pyVoIP
    from pyVoIP import RTP

    if include_unavailable:
        from pyVoIP.codecs import known_payload_types

        codecs = known_payload_types(include_events=True)
    else:
        codecs = (
            getattr(pyVoIP, "RTPCompatibleCodecs", [])
            if enabled_codecs is None
            else list(enabled_codecs)
        )

    return [
        codec_info(
            codec,
            payload_type=RTP.default_payload_type(codec),
            priority_scores=priority_scores,
            enabled_codecs=enabled_codecs,
        )
        for codec in _prioritize_payload_types(
            list(codecs),
            priority_scores=priority_scores,
        )
    ]


def _unknown_codec_info(
    *,
    media: Dict[str, Any],
    payload_type: Optional[int],
    name: str,
    rate: Optional[int],
    channels: Optional[int],
    fmtp: List[str],
    source: str,
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> Dict[str, Any]:
    return {
        "media_type": media.get("type"),
        "payload_type": payload_type,
        "name": name,
        "description": None,
        "payload_kind": "unknown",
        "can_transmit_audio": False,
        "rate": rate,
        "channels": channels,
        "is_dynamic": payload_type is None or payload_type >= 96,
        "fmtp": list(fmtp),
        "fmtp_supported": False,
        "codec_supported": False,
        "protocol_supported": _media_protocol_supported(media),
        "supported": False,
        "available": False,
        "availability_reason": "Codec is not known to PyVoIP.",
        "library": None,
        "priority_score": 0,
        "default_payload_type": None,
        "rtpmap": None,
        "bandwidth_supported": True,
        "bandwidth": _bandwidth_context(
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        ),
        "source": source,
        "protocol": _protocol_value(media.get("protocol")),
    }


def _codec_info_from_media(
    media: Dict[str, Any],
    method: str,
    *,
    session_bandwidth: Any = None,
) -> Dict[str, Any]:
    import pyVoIP
    from pyVoIP import RTP, SIP

    media_bandwidth = _bandwidths_to_list(media.get("bandwidth", []))
    session_bandwidth = _bandwidths_to_list(session_bandwidth)
    attributes = media.get("attributes", {}).get(str(method), {})
    if not isinstance(attributes, dict):
        attributes = {}

    rtpmap = attributes.get("rtpmap", {})
    if not isinstance(rtpmap, dict):
        rtpmap = {}

    fmtp = _fmtp_settings(attributes)
    payload_type = _safe_int(method)
    codec = None
    source = "unknown"
    name = str(method)
    rate = None
    channels = None

    if rtpmap:
        source = "rtpmap"
        name = str(rtpmap.get("name") or name)
        rate = _safe_int(rtpmap.get("frequency"))
        channels = _safe_int(rtpmap.get("encoding"))
        try:
            codec = RTP.payload_type_from_name(name, rate=rate, channels=channels)
        except ValueError:
            codec = None

    if codec is None and payload_type is not None:
        try:
            codec = RTP.PayloadType(payload_type)
            source = "static"
        except ValueError:
            pass

    if codec is None:
        return _unknown_codec_info(
            media=media,
            payload_type=payload_type,
            name=name,
            rate=rate,
            channels=channels,
            fmtp=fmtp,
            source=source,
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        )

    codec_supported = codec in getattr(pyVoIP, "RTPCompatibleCodecs", [])
    protocol_supported = _media_protocol_supported(media)
    fmtp_supported = RTP.codec_fmtp_supported(codec, fmtp)
    bandwidth_supported = SIP.codec_bandwidth_supported(
        codec,
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
    )
    info = codec_info(
        codec,
        payload_type=payload_type,
        media_type=media.get("type"),
        fmtp=fmtp,
        source=source,
        supported=(
            codec_supported
            and protocol_supported
            and fmtp_supported
            and bandwidth_supported
        ),
    )
    info["codec_supported"] = codec_supported
    info["protocol_supported"] = protocol_supported
    info["fmtp_supported"] = fmtp_supported
    if rate is not None:
        info["rate"] = rate
    if channels is not None:
        info["channels"] = channels
    info["bandwidth_supported"] = bandwidth_supported
    info["required_bandwidth_bps"] = _codec_required_bandwidth_bps(codec)
    info["bandwidth"] = _bandwidth_context(
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
        codec=codec,
    )
    info["protocol"] = _protocol_value(media.get("protocol"))
    return info


def sip_supported_codecs(
    message: Any,
    media_type: Optional[str] = "audio",
) -> List[Dict[str, Any]]:
    """Return codecs advertised by a parsed SIP message's SDP body."""
    session_bandwidth = _bandwidths_to_list(getattr(message, "body", {}).get("b", []))
    codecs = []
    for media in getattr(message, "body", {}).get("m", []):
        if media_type is not None and media.get("type") != media_type:
            continue
        for method in media.get("methods", []):
            codecs.append(
                _codec_info_from_media(
                    media,
                    str(method),
                    session_bandwidth=session_bandwidth,
                )
            )
    return codecs


def _codec_name_key(codec: Dict[str, Any]) -> str:
    name = str(codec.get("name") or "").lower()
    rate = codec.get("rate")
    return f"{name}/{rate}" if rate not in (None, "") else name


def sip_message_codec_report(
    message: Any,
    media_type: Optional[str] = "audio",
) -> Dict[str, Any]:
    """Compare a SIP message's SDP codecs against local PyVoIP support."""
    remote = sip_supported_codecs(message, media_type=media_type)
    pyvoip_codecs = supported_codecs()
    compatible = [codec for codec in remote if codec.get("supported")]
    unsupported = [codec for codec in remote if not codec.get("supported")]
    remote_names = {_codec_name_key(codec) for codec in remote}
    pyvoip_missing_from_remote = [
        codec for codec in pyvoip_codecs if _codec_name_key(codec) not in remote_names
    ]
    remote_has_sdp = bool(getattr(message, "body", {}).get("m"))
    transmittable_audio = [
        codec
        for codec in compatible
        if codec.get("media_type") == "audio" and codec.get("can_transmit_audio")
    ]

    return {
        "remote": remote,
        "pyvoip": pyvoip_codecs,
        "compatible": compatible,
        "unsupported": unsupported,
        "good": compatible,
        "missing": unsupported,
        "pyvoip_missing_from_remote": pyvoip_missing_from_remote,
        "remote_has_sdp": remote_has_sdp,
        "transmittable_audio": transmittable_audio,
        "call_compatible": transmittable_audio,
        "can_start_call": bool(transmittable_audio) if remote_has_sdp else None,
    }


def _prioritized_enabled_codecs(phone: Optional[Any] = None) -> List[Any]:
    import pyVoIP
    from pyVoIP import RTP

    priorities = dict(getattr(phone, "codec_priorities", {}) or {})
    indexed = list(enumerate(getattr(pyVoIP, "RTPCompatibleCodecs", [])))
    indexed.sort(
        key=lambda item: (
            -RTP.codec_priority_score(item[1], priority_scores=priorities),
            item[0],
        )
    )
    return [codec for _index, codec in indexed]


def _add_codec_to_offer(offer_codecs: Dict[int, Any], codec: Any) -> None:
    from pyVoIP import RTP

    payload_type = RTP.default_payload_type(codec)
    if payload_type is None:
        try:
            payload_type = int(codec)
        except Exception:
            payload_type = 96

    seen_payload_types = set()
    while payload_type in offer_codecs:
        if payload_type in seen_payload_types:
            raise RTP.RTPParseError("No RTP payload numbers are available for SDP offer.")
        seen_payload_types.add(payload_type)
        payload_type += 1
        if payload_type > 127:
            payload_type = 96

    offer_codecs[payload_type] = codec


def local_codec_offer(phone: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Return the audio codecs that a phone/global PyVoIP config would offer."""
    import pyVoIP
    from pyVoIP import RTP

    priority_scores = dict(getattr(phone, "codec_priorities", {}) or {})
    offer_codecs: Dict[int, Any] = {}
    for codec in _prioritized_enabled_codecs(phone):
        if RTP.is_transmittable_audio_codec(codec):
            _add_codec_to_offer(offer_codecs, codec)

    if RTP.PayloadType.EVENT in getattr(pyVoIP, "RTPCompatibleCodecs", []):
        _add_codec_to_offer(offer_codecs, RTP.PayloadType.EVENT)

    offer = []
    for payload_type, codec in offer_codecs.items():
        info = codec_info(
            codec,
            payload_type=payload_type,
            media_type="audio",
            source="local-offer",
            supported=codec in getattr(pyVoIP, "RTPCompatibleCodecs", []),
            priority_scores=priority_scores,
        )
        info["protocol"] = RTP.RTPProtocol.AVP.value
        info["protocol_supported"] = True
        info["bandwidth_supported"] = True
        info["public_audio_sample_rate"] = (
            getattr(phone, "audio_sample_rate", None)
            or info.get("preferred_source_sample_rate")
            or info.get("rate")
        )
        info["supported"] = bool(
            info["codec_supported"]
            and info["protocol_supported"]
            and info["bandwidth_supported"]
        )
        offer.append(info)
    return offer


def _local_codec_report(phone: Optional[Any] = None) -> Dict[str, Any]:
    local_codecs = supported_codecs(priority_scores=getattr(phone, "codec_priorities", None))
    local_offer = local_codec_offer(phone)
    local_transmittable_audio = [
        codec
        for codec in local_offer
        if codec.get("supported") and codec.get("can_transmit_audio")
    ]
    audio_format = (
        _phone_audio_format(phone) if phone is not None else _default_audio_format()
    )
    return {
        "local": local_codecs,
        "pyvoip": local_codecs,
        "local_offer": local_offer,
        "local_transmittable_audio": local_transmittable_audio,
        "local_can_start_call": bool(local_transmittable_audio),
        "audio_format": audio_format,
    }


def _default_audio_format() -> Dict[str, Any]:
    return {
        "sample_rate": None,
        "sample_rate_mode": "auto",
        "fallback_sample_rate": 8000,
        "sample_width": 1,
        "channels": 1,
        "encoding": "unsigned-8bit-linear",
    }


def _phone_audio_format(phone: Any) -> Dict[str, Any]:
    sample_rate = getattr(phone, "audio_sample_rate", None)
    sample_width = getattr(phone, "audio_sample_width", 1)
    channels = getattr(phone, "audio_channels", 1)
    return {
        "sample_rate": sample_rate,
        "sample_rate_mode": "auto" if sample_rate is None else "fixed",
        "fallback_sample_rate": sample_rate or 8000,
        "sample_width": sample_width,
        "channels": channels,
        "encoding": "unsigned-8bit-linear",
    }


def phone_codec_report(
    phone: Any,
    target: Optional[str] = None,
    media_type: Optional[str] = "audio",
    *,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Return local and, when requested, remote pre-call codec telemetry."""
    report_data = _local_codec_report(phone)
    if target is None:
        report_data.update(
            {
                "target": None,
                "target_uri": None,
                "source": "local",
                "response": None,
                "remote": [],
                "compatible": [],
                "unsupported": [],
                "good": [],
                "missing": [],
                "pyvoip_missing_from_remote": report_data["local"],
                "remote_has_sdp": False,
                "transmittable_audio": [],
                "call_compatible": [],
                "can_start_call": report_data["local_can_start_call"],
            }
        )
        return report_data

    target_uri = phone.sip._normalize_request_target(target)
    response = phone.sip.options(target_uri, timeout=timeout)
    remote_report = sip_message_codec_report(response, media_type=media_type)
    report_data.update(remote_report)
    status_code = int(response.status)
    report_data.update(
        {
            "target": target,
            "target_uri": target_uri,
            "source": "sip-options",
            "response": {
                "status_code": status_code,
                "phrase": response.status.phrase,
                "heading": str(response.heading, "utf8", errors="replace"),
                "has_sdp": bool(response.body.get("m")),
            },
        }
    )
    if not report_data.get("remote_has_sdp"):
        report_data["can_start_call"] = None
    return report_data


def remote_supported_codecs(
    phone: Any,
    target: str,
    media_type: Optional[str] = "audio",
    *,
    timeout: Optional[float] = None,
) -> List[Dict[str, Any]]:
    response = phone.sip.options(target, timeout=timeout)
    return sip_supported_codecs(response, media_type=media_type)


def available_codecs(
    phone: Optional[Any] = None,
    target: Optional[str] = None,
    media_type: Optional[str] = "audio",
    *,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    if phone is None:
        return _local_codec_report(None)
    return phone_codec_report(phone, target=target, media_type=media_type, timeout=timeout)


def active_codecs(call: Any) -> List[Dict[str, Any]]:
    """Return codecs currently selected by a call's RTP client(s)."""
    active = []
    for client in list(getattr(call, "RTPClients", []) or []):
        preference = getattr(client, "preference", None)
        if preference is None:
            continue
        info = codec_info(
            preference,
            payload_type=getattr(client, "preference_payload_type", None),
            media_type="audio",
            source="active-call",
            supported=True,
            priority_scores=getattr(client, "codec_priority_scores", None),
            enabled_codecs=getattr(client, "enabled_codecs", None),
        )
        info["rtp"] = {
            "local": {"ip": getattr(client, "inIP", None), "port": getattr(client, "inPort", None)},
            "remote": {"ip": getattr(client, "outIP", None), "port": getattr(client, "outPort", None)},
            "transmit_type": str(getattr(client, "sendrecv", "")),
        }
        info["public_audio_format"] = {
            "sample_rate": getattr(client, "audio_sample_rate", None),
            "sample_width": getattr(client, "audio_sample_width", 1),
            "channels": getattr(client, "audio_channels", 1),
            "encoding": "unsigned-8bit-linear",
        }
        active.append(info)
    return active


def call_codec_report(call: Any) -> Dict[str, Any]:
    """Return codec telemetry for an active or pending VoIPCall."""
    active = active_codecs(call)
    remote_message = getattr(call, "remote_sip_message", None)
    if remote_message is None:
        pyvoip_codecs = supported_codecs(priority_scores=getattr(getattr(call, "phone", None), "codec_priorities", None))
        return {
            "remote": [],
            "pyvoip": pyvoip_codecs,
            "local": pyvoip_codecs,
            "compatible": [],
            "unsupported": [],
            "good": [],
            "missing": [],
            "pyvoip_missing_from_remote": pyvoip_codecs,
            "remote_has_sdp": False,
            "transmittable_audio": [],
            "call_compatible": [],
            "can_start_call": None,
            "active_codecs": active,
        }
    report_data = sip_message_codec_report(remote_message)
    report_data["active_codecs"] = active
    return report_data


def codec_support_report(
    subject: Optional[Any] = None,
    media_type: Optional[str] = "audio",
    *,
    target: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Dispatch to the right codec telemetry report for a message, call, or phone."""
    if subject is None:
        return available_codecs()
    if hasattr(subject, "body") and hasattr(subject, "headers"):
        return sip_message_codec_report(subject, media_type=media_type)
    if hasattr(subject, "RTPClients") and hasattr(subject, "call_id"):
        return call_codec_report(subject)
    if hasattr(subject, "sip") and hasattr(subject, "_default_audio_offer"):
        return phone_codec_report(subject, target=target, media_type=media_type, timeout=timeout)
    return available_codecs(subject, target=target, media_type=media_type, timeout=timeout)


def authentication_info(subject: Any) -> Dict[str, Any]:
    """Return digest-auth telemetry for a SIPMessage, SIPClient, phone, or call."""
    from pyVoIP.SIPAuth import normalize_digest_algorithm

    telemetry = getattr(subject, "telemetry", None)
    if isinstance(telemetry, dict):
        auth = telemetry.get("auth")
        if isinstance(auth, dict):
            last_digest = auth.get("last_digest") or {}
            return {
                "source": "sip-client",
                "last_digest": dict(last_digest) if isinstance(last_digest, dict) else last_digest,
                "history": list(auth.get("history", [])),
                "algorithm": (
                    last_digest.get("algorithm")
                    if isinstance(last_digest, dict)
                    else None
                ),
                "qop": last_digest.get("qop") if isinstance(last_digest, dict) else None,
                "realm": last_digest.get("realm") if isinstance(last_digest, dict) else None,
                "header": last_digest.get("header") if isinstance(last_digest, dict) else None,
                "method": last_digest.get("method") if isinstance(last_digest, dict) else None,
            }

    if hasattr(subject, "sip"):
        return authentication_info(subject.sip)
    if hasattr(subject, "phone"):
        return authentication_info(subject.phone)

    headers = getattr(subject, "headers", {})
    params = dict(getattr(subject, "authentication", {}) or {})
    header = getattr(subject, "authentication_header", None)
    algorithm = params.get("algorithm")
    if algorithm:
        algorithm = normalize_digest_algorithm(algorithm)

    challenges: Dict[str, List[Dict[str, Any]]] = {}
    raw_challenges = getattr(subject, "authentication_challenges", {})
    if isinstance(raw_challenges, dict):
        for challenge_header, entries in raw_challenges.items():
            normalized_entries = []
            for entry in entries if isinstance(entries, list) else [entries]:
                if not isinstance(entry, dict):
                    continue
                normalized = dict(entry)
                if normalized.get("algorithm"):
                    normalized["algorithm"] = normalize_digest_algorithm(normalized["algorithm"])
                normalized_entries.append(normalized)
            challenges[str(challenge_header)] = normalized_entries

    return {
        "source": "sip-message",
        "header": header,
        "algorithm": algorithm,
        "qop": params.get("qop"),
        "realm": params.get("realm"),
        "username": params.get("username"),
        "uri": params.get("uri"),
        "has_response": bool(params.get("response")),
        "has_nonce": bool(params.get("nonce")),
        "params": params,
        "challenges": challenges,
        "authorization_present": bool(headers.get("Authorization")),
        "proxy_authorization_present": bool(headers.get("Proxy-Authorization")),
    }


def _parse_path(path: Union[str, Sequence[Any]]) -> List[Any]:
    if isinstance(path, (list, tuple)):
        return list(path)

    path_text = str(path or "")
    tokens: List[Any] = []
    position = 0
    while position < len(path_text):
        if path_text[position] == ".":
            position += 1
            continue
        match = _PATH_TOKEN_RE.match(path_text, position)
        if match is None:
            return []
        if match.group("name") is not None:
            tokens.append(match.group("name"))
        else:
            raw = match.group("bracket").strip()
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                raw = raw[1:-1].encode("utf-8").decode("unicode_escape")
            else:
                maybe_int = _safe_int(raw)
                raw = maybe_int if maybe_int is not None else raw
            tokens.append(raw)
        position = match.end()
    return tokens


def _resolve_one(current: Any, token: Any) -> Any:
    if current is _MISSING:
        return _MISSING
    if isinstance(current, dict):
        return current.get(token, _MISSING)
    if isinstance(current, (list, tuple)) and isinstance(token, int):
        if -len(current) <= token < len(current):
            return current[token]
        return _MISSING
    if isinstance(token, int):
        try:
            return current[token]
        except Exception:
            return _MISSING
    if hasattr(current, str(token)):
        return getattr(current, str(token))
    try:
        return current[token]
    except Exception:
        return _MISSING


def get(source: Any, path: Union[str, Sequence[Any]], default: Any = None) -> Any:
    """Return one telemetry value by dotted/bracket path without side effects.

    Examples: ``Telemetry.get(phone, "sip.telemetry.auth.last_digest.algorithm")``
    or ``Telemetry.get(call, "RTPClients[0].preference")``.
    """
    current = source
    for token in _parse_path(path):
        current = _resolve_one(current, token)
        if current is _MISSING:
            return default
    return current


def snapshot(subject: Any) -> Dict[str, Any]:
    """Return a compact structured telemetry snapshot for a message, client, phone, or call."""
    if hasattr(subject, "RTPClients") and hasattr(subject, "call_id"):
        return {
            "type": "call",
            "call_id": getattr(subject, "call_id", None),
            "state": str(getattr(getattr(subject, "state", None), "value", getattr(subject, "state", None))),
            "auth": authentication_info(subject),
            "codecs": call_codec_report(subject),
            "active_codecs": active_codecs(subject),
        }
    if hasattr(subject, "sip") and hasattr(subject, "_default_audio_offer"):
        calls = getattr(subject, "calls", {})
        return {
            "type": "phone",
            "status": str(getattr(getattr(subject, "_status", None), "value", getattr(subject, "_status", None))),
            "audio_format": _phone_audio_format(subject),
            "auth": authentication_info(subject),
            "codecs": phone_codec_report(subject),
            "calls": {call_id: str(getattr(getattr(call, "state", None), "value", getattr(call, "state", None))) for call_id, call in calls.items()},
        }
    if hasattr(subject, "body") and hasattr(subject, "headers"):
        return {
            "type": "sip-message",
            "heading": str(getattr(subject, "heading", b""), "utf8", errors="replace") if isinstance(getattr(subject, "heading", b""), bytes) else str(getattr(subject, "heading", "")),
            "auth": authentication_info(subject),
            "codecs": sip_message_codec_report(subject),
        }
    return {
        "type": type(subject).__name__,
        "auth": authentication_info(subject),
    }


def _escape_telegram_markdown_v2(text: Any) -> str:
    escaped = str(text)
    for char in _MARKDOWN_SPECIALS_V2:
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _bold(text: str, markdown: str) -> str:
    if markdown.lower().startswith("telegram"):
        if markdown.lower() in ("telegram-v2", "telegram_markdown_v2", "telegramv2"):
            return "*" + _escape_telegram_markdown_v2(text) + "*"
        return f"*{text}*"
    return f"**{text}**"


def _plain(text: Any, markdown: str) -> str:
    if markdown.lower() in ("telegram-v2", "telegram_markdown_v2", "telegramv2"):
        return _escape_telegram_markdown_v2(text)
    return str(text)


def _format_codecs(codecs: List[Dict[str, Any]], *, limit: int = 5) -> str:
    names = []
    for item in codecs[:limit]:
        name = item.get("name") or item.get("description") or "unknown"
        rate = item.get("rate")
        payload = item.get("payload_type")
        label = f"{name}/{rate}" if rate not in (None, "") else str(name)
        if payload is not None:
            label += f" pt={payload}"
        names.append(label)
    if len(codecs) > limit:
        names.append(f"+{len(codecs) - limit} more")
    return ", ".join(names) if names else "none"


def _status_text(subject: Any) -> str:
    if hasattr(subject, "_status"):
        status = getattr(subject, "_status")
        return str(getattr(status, "value", status))
    if hasattr(subject, "state"):
        state = getattr(subject, "state")
        return str(getattr(state, "value", state))
    return "unknown"


def report(
    subject: Optional[Any] = None,
    *,
    target: Optional[str] = None,
    media_type: Optional[str] = "audio",
    timeout: Optional[float] = None,
    markdown: str = "discord",
) -> str:
    """Return a concise emoji telemetry report for Discord or Telegram Markdown.

    Use ``markdown="discord"`` for Discord, ``markdown="telegram"`` for
    Telegram legacy Markdown, and ``markdown="telegram-v2"`` for Telegram
    MarkdownV2 escaping.
    """
    lines: List[str] = []
    title = "pyVoIP telemetry"
    lines.append(f"☎️ {_bold(title, markdown)}")

    if subject is None:
        data = codec_support_report(None, media_type=media_type)
        lines.append(f"🧩 {_bold('Local codecs', markdown)}: {_plain(_format_codecs(data.get('local_offer', [])), markdown)}")
        lines.append(f"🎚️ {_bold('Audio', markdown)}: {_plain(data.get('audio_format', {}).get('sample_rate_mode', 'auto'), markdown)}")
        return "\n".join(lines)

    auth = authentication_info(subject)
    auth_algo = auth.get("algorithm") or get(auth, "last_digest.algorithm") or "not used"
    auth_header = auth.get("header") or get(auth, "last_digest.header") or "n/a"
    auth_qop = auth.get("qop") or get(auth, "last_digest.qop")

    if hasattr(subject, "RTPClients") and hasattr(subject, "call_id"):
        data = call_codec_report(subject)
        lines.append(f"📞 {_bold('Call', markdown)}: {_plain(getattr(subject, 'call_id', 'unknown'), markdown)}")
        lines.append(f"🔁 {_bold('State', markdown)}: {_plain(_status_text(subject), markdown)}")
        lines.append(f"🔐 {_bold('Digest', markdown)}: {_plain(str(auth_algo) + (f' qop={auth_qop}' if auth_qop else '') + f' via {auth_header}', markdown)}")
        lines.append(f"🎧 {_bold('Active audio', markdown)}: {_plain(_format_codecs(data.get('active_codecs', [])), markdown)}")
        return "\n".join(lines)

    if hasattr(subject, "sip") and hasattr(subject, "_default_audio_offer"):
        data = phone_codec_report(subject, target=target, media_type=media_type, timeout=timeout)
        calls = getattr(subject, "calls", {})
        lines.append(f"📟 {_bold('Phone status', markdown)}: {_plain(_status_text(subject), markdown)}")
        lines.append(f"🔐 {_bold('Digest', markdown)}: {_plain(str(auth_algo) + (f' qop={auth_qop}' if auth_qop else '') + f' via {auth_header}', markdown)}")
        lines.append(f"🧩 {_bold('Offer', markdown)}: {_plain(_format_codecs(data.get('local_offer', [])), markdown)}")
        if target is not None:
            response = data.get("response") or {}
            remote = _format_codecs(data.get("remote", []))
            status = response.get("status_code", "?")
            phrase = response.get("phrase", "")
            lines.append(f"🌐 {_bold('Remote', markdown)}: {_plain(f'{target} → SIP {status} {phrase}'.strip(), markdown)}")
            lines.append(f"🤝 {_bold('Compatible', markdown)}: {_plain(_format_codecs(data.get('transmittable_audio', [])) or remote, markdown)}")
        lines.append(f"📞 {_bold('Calls', markdown)}: {_plain(len(calls), markdown)}")
        return "\n".join(lines)

    if hasattr(subject, "body") and hasattr(subject, "headers"):
        data = sip_message_codec_report(subject, media_type=media_type)
        heading = getattr(subject, "heading", b"")
        if isinstance(heading, bytes):
            heading_text = str(heading, "utf8", errors="replace")
        else:
            heading_text = str(heading)
        lines.append(f"📨 {_bold('SIP', markdown)}: {_plain(heading_text, markdown)}")
        lines.append(f"🔐 {_bold('Digest', markdown)}: {_plain(str(auth_algo) + (f' qop={auth_qop}' if auth_qop else '') + f' via {auth_header}', markdown)}")
        lines.append(f"🧩 {_bold('Remote codecs', markdown)}: {_plain(_format_codecs(data.get('remote', [])), markdown)}")
        lines.append(f"🤝 {_bold('Compatible', markdown)}: {_plain(_format_codecs(data.get('transmittable_audio', [])), markdown)}")
        return "\n".join(lines)

    data = snapshot(subject)
    lines.append(f"📦 {_bold('Object', markdown)}: {_plain(data.get('type'), markdown)}")
    lines.append(f"🔐 {_bold('Digest', markdown)}: {_plain(str(auth_algo) + (f' qop={auth_qop}' if auth_qop else '') + f' via {auth_header}', markdown)}")
    return "\n".join(lines)