from __future__ import annotations

from copy import deepcopy
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pyVoIP


__all__ = [
    "auth_snapshot",
    "call_active_codecs",
    "call_codec_report",
    "call_remote_supported_codecs",
    "call_snapshot",
    "codec_availability",
    "codec_info",
    "codec_support_report",
    "discord_report",
    "get",
    "local_codec_offer",
    "local_codec_report",
    "local_supported_codecs",
    "phone_codec_report",
    "phone_snapshot",
    "record_digest_auth",
    "remote_supported_codecs",
    "report",
    "rtp_client_codec_info",
    "sip_client_snapshot",
    "sip_message_snapshot",
    "sip_supported_codecs",
    "snapshot",
    "supported_codecs",
    "telegram_report",
]


_SDP_MEDIA_BANDWIDTH_LIMIT_TYPES = {"AS", "TIAS"}
_TELEGRAM_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!"


_PROCESS_TELEMETRY: Dict[str, Any] = {
    "auth": {
        "last_digest": None,
        "digest_history": [],
    }
}

# Frontends often wrap the real pyVoIP objects.
# Authentication telemetry is recorded on SIPClient, so auth lookups must walk
# a small but explicit set of wrapper attributes.
_TELEMETRY_SOURCE_ATTRS = (
    "sip",
    "_sip",
    "phone",
    "_phone",
    "call",
    "_call",
    "client",
    "_client",
    "backend",
    "_backend",
    "request",
    "remote_sip_message",
)


def _empty_auth_snapshot(**extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "last_digest": None,
        "digest_history": [],
        "has_authenticated": False,
    }
    data.update(extra)
    return data


def _iter_source_candidates(source: Any, *, max_depth: int = 4):
    """Yield ``source`` and likely wrapped pyVoIP objects without cycles."""
    seen = set()

    def visit(obj: Any, depth: int):
        if obj is None:
            return
        obj_id = id(obj)
        if obj_id in seen:
            return
        seen.add(obj_id)
        yield obj
        if depth <= 0:
            return

        if isinstance(obj, dict):
            for key in _TELEMETRY_SOURCE_ATTRS:
                child = obj.get(key)
                if child is not None and child is not obj:
                    yield from visit(child, depth - 1)
            return

        for attr in _TELEMETRY_SOURCE_ATTRS:
            try:
                child = getattr(obj, attr)
            except Exception:
                continue
            if child is not None and child is not obj:
                yield from visit(child, depth - 1)

    yield from visit(source, max_depth)


def _normalize_auth_block(auth: Any) -> Dict[str, Any]:
    if not isinstance(auth, dict):
        return _empty_auth_snapshot()

    data = deepcopy(auth)
    history = data.get("digest_history")
    if history is None:
        history = data.get("history")
    if history is None:
        history = []
    elif isinstance(history, tuple):
        history = list(history)
    elif not isinstance(history, list):
        history = [history]
    history = [dict(item) for item in history if isinstance(item, dict)]

    last_digest = data.get("last_digest")
    if last_digest is None:
        last_digest = data.get("last") or data.get("digest")
    if not isinstance(last_digest, dict) and history:
        last_digest = history[-1]

    data["last_digest"] = (
        deepcopy(last_digest) if isinstance(last_digest, dict) else None
    )
    data["digest_history"] = deepcopy(history)
    data["has_authenticated"] = bool(
        data["last_digest"] or history or data.get("has_authenticated")
    )
    return data


def _auth_block_from_candidate(candidate: Any) -> Optional[Dict[str, Any]]:
    if isinstance(candidate, dict):
        if "last_digest" in candidate or "digest_history" in candidate:
            return _normalize_auth_block(candidate)
        if "auth" in candidate:
            return _normalize_auth_block(candidate.get("auth"))
        return None

    telemetry = getattr(candidate, "_telemetry", None)
    if isinstance(telemetry, dict):
        auth = _normalize_auth_block(telemetry.get("auth", {}))
        if auth.get("last_digest") or auth.get("digest_history"):
            return auth

    for attr in (
        "auth_telemetry",
        "_auth_telemetry",
        "digest_auth_telemetry",
        "_digest_auth_telemetry",
        "last_digest_auth",
        "_last_digest_auth",
    ):
        try:
            value = getattr(candidate, attr)
        except Exception:
            continue
        if value is None:
            continue
        if attr.startswith("last") or attr.startswith("_last"):
            return _normalize_auth_block({"last_digest": value})
        auth = _normalize_auth_block(value)
        if auth.get("last_digest") or auth.get("digest_history"):
            return auth

    return None


def _is_sip_client_like(source: Any) -> bool:
    if source is None:
        return False
    if source.__class__.__name__ == "SIPClient":
        return True
    if isinstance(getattr(source, "_telemetry", None), dict) and (
        hasattr(source, "signal_target")
        or hasattr(source, "signal_transport")
        or hasattr(source, "_build_digest_auth_header")
    ):
        return True
    return False


def _unwrap_snapshot_source(source: Any) -> Any:
    """Prefer the real pyVoIP phone/SIP/call object inside frontend wrappers."""
    if source is None or isinstance(source, dict):
        return source

    if (
        (hasattr(source, "calls") and hasattr(source, "sip"))
        or (hasattr(source, "RTPClients") and hasattr(source, "call_id"))
        or (
            hasattr(source, "headers")
            and hasattr(source, "body")
            and hasattr(source, "type")
        )
        or _is_sip_client_like(source)
    ):
        return source

    candidates = list(_iter_source_candidates(source))
    for candidate in candidates[1:]:
        if hasattr(candidate, "calls") and hasattr(candidate, "sip"):
            return candidate
    for candidate in candidates[1:]:
        if hasattr(candidate, "RTPClients") and hasattr(candidate, "call_id"):
            return candidate
    for candidate in candidates[1:]:
        if _is_sip_client_like(candidate):
            return candidate
    for candidate in candidates[1:]:
        if isinstance(getattr(candidate, "_telemetry", None), dict):
            return candidate
    return source


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


def _enforceable_bandwidth_limit_bps(
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> Optional[int]:
    limits: List[int] = []
    for bandwidth in _bandwidths_to_list(session_bandwidth) + _bandwidths_to_list(
        media_bandwidth
    ):
        bw_type = str(bandwidth.get("type", "")).upper()
        if bw_type not in _SDP_MEDIA_BANDWIDTH_LIMIT_TYPES:
            continue
        limit = _safe_int(bandwidth.get("bits_per_second"))
        if limit is not None:
            limits.append(limit)
    return min(limits) if limits else None


def _codec_required_bandwidth_bps(codec: Any) -> Optional[int]:
    try:
        from pyVoIP.codecs import codec_required_bandwidth_bps as required_bps

        return required_bps(codec)
    except Exception:
        return None


def _codec_bandwidth_supported(
    codec: Any,
    *,
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
) -> bool:
    required = _codec_required_bandwidth_bps(codec)
    limit = _enforceable_bandwidth_limit_bps(
        session_bandwidth=session_bandwidth,
        media_bandwidth=media_bandwidth,
    )
    return required is None or limit is None or limit >= required


def _bandwidth_context(
    session_bandwidth: Any = None,
    media_bandwidth: Any = None,
    codec: Any = None,
) -> Dict[str, Any]:
    return {
        "session": _bandwidths_to_list(session_bandwidth),
        "media": _bandwidths_to_list(media_bandwidth),
        "limit_bps": _enforceable_bandwidth_limit_bps(
            session_bandwidth=session_bandwidth,
            media_bandwidth=media_bandwidth,
        ),
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


def _codec_name_key(codec: Dict[str, Any]) -> str:
    name = str(codec.get("name") or "").lower()
    rate = codec.get("rate")
    return f"{name}/{rate}" if rate not in (None, "") else name


def _codec_availability_details(codec: Any) -> Dict[str, Any]:
    from pyVoIP import RTP

    try:
        from pyVoIP.codecs import codec_availability as _availability

        return dict(_availability(codec))
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

    Pass one payload type for a single record. With no payload type, all known
    codec implementations are returned, including optional codecs that are not
    currently available.
    """
    if codec is not None:
        return _codec_availability_details(codec)

    from pyVoIP.codecs import availability_report

    return list(availability_report(refresh=refresh))


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
    """Return a serializable telemetry record for one RTP codec."""
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
        "rate": getattr(codec, "rate", 0),
        "channels": getattr(codec, "channel", 0),
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
        "required_bandwidth_bps": availability.get("required_bandwidth_bps"),
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


def local_supported_codecs(
    include_unavailable: bool = False,
    *,
    priority_scores: Optional[Dict[Any, int]] = None,
    enabled_codecs: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return local codec telemetry for this PyVoIP build/configuration."""
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


def supported_codecs(
    include_unavailable: bool = False,
    *,
    priority_scores: Optional[Dict[Any, int]] = None,
    enabled_codecs: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Alias for local_supported_codecs()."""
    return local_supported_codecs(
        include_unavailable=include_unavailable,
        priority_scores=priority_scores,
        enabled_codecs=enabled_codecs,
    )


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
    from pyVoIP import RTP

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
            codec = RTP.payload_type_from_name(
                name,
                rate=rate,
                channels=channels,
            )
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
    bandwidth_supported = _codec_bandwidth_supported(
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
    body = getattr(message, "body", {}) or {}
    session_bandwidth = _bandwidths_to_list(body.get("b", []))
    codecs: List[Dict[str, Any]] = []
    for media in body.get("m", []):
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


def codec_support_report(
    message: Any,
    media_type: Optional[str] = "audio",
) -> Dict[str, Any]:
    """Compare a SIP message's SDP codecs against PyVoIP support."""
    remote = sip_supported_codecs(message, media_type=media_type)
    pyvoip_codecs = local_supported_codecs()
    compatible = [codec for codec in remote if codec.get("supported")]
    unsupported = [codec for codec in remote if not codec.get("supported")]
    remote_names = {_codec_name_key(codec) for codec in remote}
    pyvoip_missing_from_remote = [
        codec for codec in pyvoip_codecs if _codec_name_key(codec) not in remote_names
    ]
    remote_has_sdp = bool((getattr(message, "body", {}) or {}).get("m"))
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


def _phone_priority_scores(phone: Optional[Any] = None) -> Dict[Any, int]:
    return dict(getattr(phone, "codec_priorities", {}) or {})


def _prioritized_enabled_codecs(phone: Optional[Any] = None) -> List[Any]:
    from pyVoIP import RTP

    priority_scores = _phone_priority_scores(phone)
    indexed = list(enumerate(getattr(pyVoIP, "RTPCompatibleCodecs", [])))
    indexed.sort(
        key=lambda item: (
            -RTP.codec_priority_score(item[1], priority_scores=priority_scores),
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
            raise RTP.RTPParseError(
                "No RTP payload numbers are available for SDP offer."
            )
        seen_payload_types.add(payload_type)
        payload_type += 1
        if payload_type > 127:
            payload_type = 96

    offer_codecs[payload_type] = codec


def local_codec_offer(phone: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Return the audio codecs a phone would offer in an INVITE."""
    from pyVoIP import RTP

    offer_codecs: Dict[int, Any] = {}
    priority_scores = _phone_priority_scores(phone)
    audio_sample_rate = getattr(phone, "audio_sample_rate", None)

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
            audio_sample_rate
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


def local_codec_report(phone: Optional[Any] = None) -> Dict[str, Any]:
    """Return local codec telemetry and the INVITE offer for a phone."""
    local_codecs = local_supported_codecs(
        priority_scores=_phone_priority_scores(phone),
    )
    offer = local_codec_offer(phone)
    local_transmittable_audio = [
        codec
        for codec in offer
        if codec.get("supported") and codec.get("can_transmit_audio")
    ]
    audio_format = None
    if phone is not None:
        audio_format_fn = getattr(phone, "audio_format", None)
        if callable(audio_format_fn):
            audio_format = audio_format_fn()
    return {
        "local": local_codecs,
        "pyvoip": local_codecs,
        "local_offer": offer,
        "local_transmittable_audio": local_transmittable_audio,
        "local_can_start_call": bool(local_transmittable_audio),
        "audio_format": audio_format,
    }


def remote_supported_codecs(
    phone: Any,
    target: str,
    media_type: Optional[str] = "audio",
    *,
    timeout: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Probe a remote target with SIP OPTIONS and return SDP codec telemetry."""
    response = phone.sip.options(target, timeout=timeout)
    return sip_supported_codecs(response, media_type=media_type)


def phone_codec_report(
    phone: Any,
    target: Optional[str] = None,
    media_type: Optional[str] = "audio",
    *,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Return local codec telemetry and optionally remote OPTIONS telemetry."""
    codec_report = local_codec_report(phone)
    if target is None:
        codec_report.update(
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
                "pyvoip_missing_from_remote": codec_report["local"],
                "remote_has_sdp": False,
                "transmittable_audio": [],
                "call_compatible": [],
                "can_start_call": codec_report["local_can_start_call"],
            }
        )
        return codec_report

    target_uri = phone.sip._normalize_request_target(target)
    response = phone.sip.options(target_uri, timeout=timeout)
    remote_report = codec_support_report(response, media_type=media_type)
    codec_report.update(remote_report)
    status_code = int(response.status)
    codec_report.update(
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
    if not codec_report.get("remote_has_sdp"):
        codec_report["can_start_call"] = None
    return codec_report


def rtp_client_codec_info(client: Any) -> Dict[str, Any]:
    """Return active codec telemetry for one RTPClient-like object."""
    selected = codec_info(
        client.preference,
        payload_type=client.preference_payload_type,
        media_type="audio",
        source="active-call",
        supported=True,
        priority_scores=getattr(client, "codec_priority_scores", None),
        enabled_codecs=getattr(client, "enabled_codecs", None),
    )
    selected["rtp"] = {
        "local": {"ip": client.inIP, "port": client.inPort},
        "remote": {"ip": client.outIP, "port": client.outPort},
        "transmit_type": str(client.sendrecv),
    }
    selected["public_audio_format"] = {
        "sample_rate": client.audio_sample_rate,
        "sample_width": client.audio_sample_width,
        "channels": client.audio_channels,
        "encoding": "unsigned-8bit-linear",
    }
    return selected


def _rtp_clients_snapshot(call: Any) -> List[Any]:
    getter = getattr(call, "_rtp_clients_snapshot", None)
    if callable(getter):
        return list(getter())
    return list(getattr(call, "RTPClients", []) or [])


def call_active_codecs(call: Any) -> List[Dict[str, Any]]:
    """Return codecs selected by a call's RTP clients."""
    active = []
    for client in _rtp_clients_snapshot(call):
        legacy_selected_codec_info = getattr(client, "selected_codec_info", None)
        if callable(legacy_selected_codec_info):
            try:
                info = legacy_selected_codec_info()
                if isinstance(info, dict):
                    active.append(dict(info))
                    continue
            except Exception:
                pass

        try:
            active.append(rtp_client_codec_info(client))
        except Exception as ex:
            active.append({"supported": False, "error": str(ex), "source": "active-call"})
    return active


def call_remote_supported_codecs(call: Any) -> List[Dict[str, Any]]:
    """Return codecs advertised by the remote endpoint for a call."""
    remote_sip_message = getattr(call, "remote_sip_message", None)
    if remote_sip_message is None:
        return []
    return sip_supported_codecs(remote_sip_message)


def call_codec_report(call: Any) -> Dict[str, Any]:
    """Return codec telemetry for a VoIPCall-like object."""
    active_codecs = call_active_codecs(call)
    remote_sip_message = getattr(call, "remote_sip_message", None)
    if remote_sip_message is None:
        pyvoip_codecs = local_supported_codecs(
            priority_scores=_phone_priority_scores(getattr(call, "phone", None))
        )
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
            "active_codecs": active_codecs,
        }
    codec_report = codec_support_report(remote_sip_message)
    codec_report["active_codecs"] = active_codecs
    return codec_report


def _normalize_digest_algorithm_for_display(value: Any) -> str:
    try:
        from pyVoIP.SIPAuth import normalize_digest_algorithm

        return normalize_digest_algorithm(value)
    except Exception:
        return str(value or "MD5")


def _message_auth_challenges(message: Any) -> List[Dict[str, Any]]:
    challenges: List[Dict[str, Any]] = []
    raw_challenges = getattr(message, "authentication_challenges", {}) or {}
    if isinstance(raw_challenges, dict):
        for header, items in raw_challenges.items():
            if isinstance(items, dict):
                iterable = [items]
            else:
                iterable = items or []
            for item in iterable:
                if not isinstance(item, dict):
                    continue
                challenges.append(
                    {
                        "header": header,
                        "algorithm": _normalize_digest_algorithm_for_display(
                            item.get("algorithm")
                        ),
                        "realm": item.get("realm"),
                        "qop": item.get("qop"),
                        "nonce_present": bool(item.get("nonce")),
                        "opaque_present": bool(item.get("opaque")),
                    }
                )
    return challenges


def _digest_params_from_header_value(value: Any) -> Dict[str, str]:
    if isinstance(value, dict):
        return {
            str(key).lower(): str(val)
            for key, val in value.items()
            if val is not None
        }
    try:
        from pyVoIP.SIPAuth import parse_digest_params

        return parse_digest_params(str(value or ""))
    except Exception:
        return {}


def _message_authorization_record(message: Any) -> Optional[Dict[str, Any]]:
    headers = getattr(message, "headers", {}) or {}
    if not isinstance(headers, dict):
        headers = {}

    candidates: List[Tuple[str, Any]] = []
    for header in ("Authorization", "Proxy-Authorization"):
        value = headers.get(header)
        if value is None:
            continue
        if isinstance(value, list):
            candidates.extend((header, item) for item in value)
        else:
            candidates.append((header, value))

    if not candidates and getattr(message, "authentication", None):
        header = getattr(message, "authentication_header", None) or "Authorization"
        candidates.append((str(header), getattr(message, "authentication")))

    for header, value in candidates:
        params = _digest_params_from_header_value(value)
        if not params:
            continue
        if not any(
            key in params
            for key in ("response", "uri", "username", "qop", "cnonce", "nc")
        ):
            continue

        cseq = headers.get("CSeq", {})
        cseq_method = cseq.get("method") if isinstance(cseq, dict) else None
        return {
            "algorithm": _normalize_digest_algorithm_for_display(
                params.get("algorithm")
            ),
            "qop": params.get("qop"),
            "header": header,
            "challenge_header": (
                "Proxy-Authenticate"
                if header == "Proxy-Authorization"
                else "WWW-Authenticate"
            ),
            "method": cseq_method or getattr(message, "method", None),
            "uri": params.get("uri") or getattr(message, "uri", None),
            "realm": params.get("realm"),
            "username": params.get("username"),
            "nonce_present": bool(params.get("nonce")),
            "opaque_present": bool(params.get("opaque")),
            "body_hashing": str(params.get("qop") or "").lower() == "auth-int",
            "source": "sip-message-authorization",
            "recorded_at": None,
        }

    return None


def _message_auth_snapshot(message: Any) -> Dict[str, Any]:
    record = _message_authorization_record(message)
    return _empty_auth_snapshot(
        last_digest=record,
        digest_history=[record] if record else [],
        has_authenticated=bool(record),
        message_authentication_header=getattr(message, "authentication_header", None),
        challenges=_message_auth_challenges(message),
    )


def _store_auth_record(telemetry: Dict[str, Any], record: Dict[str, Any]) -> None:
    auth = telemetry.setdefault("auth", {})
    if not isinstance(auth, dict):
        auth = {}
        telemetry["auth"] = auth
    auth["last_digest"] = dict(record)
    auth["has_authenticated"] = True
    auth["updated_at"] = record.get("recorded_at", time.time())
    history = auth.setdefault("digest_history", [])
    if not isinstance(history, list):
        history = []
        auth["digest_history"] = history
    history.append(dict(record))
    if len(history) > 20:
        del history[:-20]


def record_digest_auth(source: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """Record non-secret metadata about the selected SIP digest algorithm."""
    if not isinstance(record, dict):
        record = {"value": record}
    record = deepcopy(record)
    record.setdefault("recorded_at", time.time())
    record.setdefault("source", "sip-digest-auth")

    stored = False
    for candidate in _iter_source_candidates(source):
        if candidate is None or isinstance(candidate, dict):
            continue
        telemetry = getattr(candidate, "_telemetry", None)
        if not isinstance(telemetry, dict):
            if not _is_sip_client_like(candidate):
                continue
            telemetry = {}
            try:
                setattr(candidate, "_telemetry", telemetry)
            except Exception:
                continue
        _store_auth_record(telemetry, record)
        stored = True
        break

    _store_auth_record(_PROCESS_TELEMETRY, record)
    if not stored and isinstance(source, dict):
        _store_auth_record(source, record)
    return record


def auth_snapshot(source: Any) -> Dict[str, Any]:
    """Return SIP authentication telemetry for a phone, SIP client, message, or wrapper."""
    fallback: Optional[Dict[str, Any]] = None

    for candidate in _iter_source_candidates(source):
        auth = _auth_block_from_candidate(candidate)
        if auth is not None:
            if auth.get("last_digest"):
                return auth
            if fallback is None and auth.get("digest_history"):
                fallback = auth

        if (
            hasattr(candidate, "authentication")
            or hasattr(candidate, "authentication_challenges")
            or isinstance(getattr(candidate, "headers", None), dict)
        ):
            auth = _message_auth_snapshot(candidate)
            if auth.get("last_digest"):
                return auth
            if fallback is None and auth.get("challenges"):
                fallback = auth

    process_auth = _normalize_auth_block(_PROCESS_TELEMETRY.get("auth", {}))
    if process_auth.get("last_digest"):
        process_auth["source"] = "process-fallback"
        return process_auth

    return fallback or _empty_auth_snapshot()


def sip_client_snapshot(client: Any) -> Dict[str, Any]:
    """Return signaling and auth telemetry for a SIPClient-like object."""
    target = None
    transport = None
    try:
        signal_host, signal_port = client.signal_target()
        target = {"host": signal_host, "port": signal_port}
    except Exception:
        pass
    try:
        transport = str(client.signal_transport().value)
    except Exception:
        transport = str(getattr(client, "requested_transport", "") or "") or None

    return {
        "type": "sip-client",
        "running": bool(getattr(client, "NSD", False)),
        "server": getattr(client, "server", None),
        "server_host": getattr(client, "server_host", None),
        "server_port": getattr(client, "server_port", None),
        "proxy": getattr(client, "proxy", None),
        "proxy_port": getattr(client, "proxy_port", None),
        "target": target,
        "transport": transport,
        "my_ip": getattr(client, "myIP", None),
        "my_port": getattr(client, "myPort", None),
        "auth": auth_snapshot(client),
    }


def sip_message_snapshot(
    message: Any,
    media_type: Optional[str] = "audio",
) -> Dict[str, Any]:
    """Return concise telemetry for one parsed SIPMessage-like object."""
    cseq = getattr(message, "headers", {}).get("CSeq", {})
    cseq_method = cseq.get("method") if isinstance(cseq, dict) else None
    cseq_check = cseq.get("check") if isinstance(cseq, dict) else None
    status = getattr(message, "status", None)
    code = None
    phrase = None
    if status is not None:
        try:
            code = int(status)
        except Exception:
            code = None
        phrase = getattr(status, "phrase", None)

    codec_report = codec_support_report(message, media_type=media_type)
    return {
        "type": "sip-message",
        "message_type": str(getattr(message, "type", "")),
        "method": getattr(message, "method", None),
        "status_code": code,
        "status_phrase": phrase,
        "call_id": getattr(message, "headers", {}).get("Call-ID"),
        "cseq": {"check": cseq_check, "method": cseq_method},
        "content_type": getattr(message, "headers", {}).get("Content-Type"),
        "has_sdp": bool((getattr(message, "body", {}) or {}).get("m")),
        "auth": auth_snapshot(message),
        "codecs": codec_report,
    }


def call_snapshot(call: Any) -> Dict[str, Any]:
    """Return telemetry for a VoIPCall-like object."""
    state = getattr(call, "state", None)
    state_value = getattr(state, "value", str(state) if state is not None else None)
    assigned_ports = getattr(call, "assignedPorts", {})
    if hasattr(assigned_ports, "keys"):
        ports = list(assigned_ports.keys())
    else:
        ports = list(assigned_ports or [])

    return {
        "type": "call",
        "call_id": getattr(call, "call_id", None),
        "state": state_value,
        "session_id": getattr(call, "session_id", None),
        "sendmode": str(getattr(call, "sendmode", "")),
        "local_ports": ports,
        "codecs": call_codec_report(call),
        "auth": auth_snapshot(getattr(call, "sip", None)),
    }


def phone_snapshot(phone: Any, media_type: Optional[str] = "audio") -> Dict[str, Any]:
    """Return telemetry for a VoIPPhone-like object."""
    status = getattr(phone, "_status", None)
    status_value = getattr(status, "value", str(status) if status is not None else None)
    calls_getter = getattr(phone, "_calls_snapshot", None)
    calls = calls_getter() if callable(calls_getter) else list(getattr(phone, "calls", {}).values())
    sip = getattr(phone, "sip", None)

    return {
        "type": "phone",
        "package": {"name": "pyVoIP", "version": getattr(pyVoIP, "__version__", None)},
        "generated_at": time.time(),
        "phone": {
            "status": status_value,
            "running": bool(getattr(phone, "NSD", False)),
            "server": getattr(phone, "server", None),
            "port": getattr(phone, "port", None),
            "my_ip": getattr(phone, "myIP", None),
            "username": getattr(phone, "username", None),
            "proxy": getattr(phone, "proxy", None),
            "proxy_port": getattr(phone, "proxyPort", None),
            "transport": getattr(phone, "transport", None),
            "rtp_port_low": getattr(phone, "rtpPortLow", None),
            "rtp_port_high": getattr(phone, "rtpPortHigh", None),
            "assigned_ports": list(getattr(phone, "assignedPorts", []) or []),
            "audio_format": phone.audio_format() if callable(getattr(phone, "audio_format", None)) else None,
        },
        "sip": sip_client_snapshot(sip) if sip is not None else None,
        "auth": auth_snapshot(sip) if sip is not None else auth_snapshot(phone),
        "codecs": phone_codec_report(phone, media_type=media_type),
        "calls": [call_snapshot(call) for call in calls],
    }


def snapshot(source: Optional[Any] = None, *, media_type: Optional[str] = "audio") -> Dict[str, Any]:
    """Return a serializable telemetry snapshot for a phone, call, SIP object, or package."""
    if isinstance(source, dict):
        return deepcopy(source)

    source = _unwrap_snapshot_source(source)

    if source is None:
        return {
            "type": "package",
            "package": {"name": "pyVoIP", "version": getattr(pyVoIP, "__version__", None)},
            "generated_at": time.time(),
            "auth": auth_snapshot(None),
            "codecs": local_codec_report(None),
        }

    if hasattr(source, "calls") and hasattr(source, "sip"):
        return phone_snapshot(source, media_type=media_type)
    if hasattr(source, "RTPClients") and hasattr(source, "call_id"):
        return call_snapshot(source)
    if hasattr(source, "headers") and hasattr(source, "body") and hasattr(source, "type"):
        return sip_message_snapshot(source, media_type=media_type)
    if _is_sip_client_like(source):
        snap = sip_client_snapshot(source)
        return {
            "type": "sip-client",
            "package": {"name": "pyVoIP", "version": getattr(pyVoIP, "__version__", None)},
            "generated_at": time.time(),
            "sip": snap,
            "auth": snap.get("auth"),
        }
    if source.__class__.__name__ == "RTPClient":
        return {
            "type": "rtp-client",
            "package": {"name": "pyVoIP", "version": getattr(pyVoIP, "__version__", None)},
            "generated_at": time.time(),
            "codecs": {"active_codecs": [rtp_client_codec_info(source)]},
        }

    return {
        "type": "object",
        "package": {"name": "pyVoIP", "version": getattr(pyVoIP, "__version__", None)},
        "generated_at": time.time(),
        "object_type": source.__class__.__name__,
        "auth": auth_snapshot(source),
    }


_PATH_TOKEN_RE = re.compile(r"([^.[\]]+)|\[(\d+)\]")


def _path_tokens(path: Union[str, Iterable[Any]]) -> List[Any]:
    if isinstance(path, str):
        tokens: List[Any] = []
        for match in _PATH_TOKEN_RE.finditer(path):
            key, index = match.groups()
            tokens.append(int(index) if index is not None else key)
        return tokens
    return list(path)


def get(
    source: Any,
    path: Union[str, Iterable[Any]],
    default: Any = None,
    *,
    media_type: Optional[str] = "audio",
) -> Any:
    """Surgically read a telemetry value using dot/list path notation.

    Example: ``get(phone, "auth.last_digest.algorithm")``. Authentication
    paths are resolved through :func:`auth_snapshot`, so callers may pass a
    pyVoIP object or a frontend wrapper that owns ``_phone``/``_call``.
    """
    tokens = _path_tokens(path)
    if tokens and tokens[0] == "auth" and not isinstance(source, dict):
        current: Any = {"auth": auth_snapshot(source)}
    elif len(tokens) >= 2 and tokens[0] == "sip" and tokens[1] == "auth" and not isinstance(source, dict):
        current = {"sip": {"auth": auth_snapshot(source)}}
    else:
        current = source if isinstance(source, dict) else snapshot(source, media_type=media_type)

    for token in tokens:
        try:
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, (list, tuple)) and isinstance(token, int):
                current = current[token]
            else:
                current = getattr(current, str(token))
        except (KeyError, IndexError, TypeError, AttributeError):
            return default
    return current


def _telegram_escape(text: str) -> str:
    return "".join("\\" + ch if ch in _TELEGRAM_V2_SPECIALS else ch for ch in text)


def _text(text: Any, platform: str) -> str:
    value = str(text)
    return _telegram_escape(value) if platform.startswith("telegram") else value


def _bold(text: str, platform: str) -> str:
    if platform.startswith("telegram"):
        return f"*{_telegram_escape(text)}*"
    return f"**{text}**"


def _code(value: Any, platform: str) -> str:
    text_value = "None" if value is None else str(value)
    if platform.startswith("telegram"):
        text_value = text_value.replace("\\", "\\\\").replace("`", "\\`")
    else:
        text_value = text_value.replace("`", "'")
    return f"`{text_value}`"


def _codec_label(codec: Dict[str, Any]) -> str:
    name = str(codec.get("name") or codec.get("description") or "unknown")
    payload_type = codec.get("payload_type")
    rate = codec.get("rate")
    label = name
    if rate not in (None, "", 0):
        label += f"/{rate}"
    if payload_type is not None:
        label += f":{payload_type}"
    return label


def _codec_summary(
    codecs: List[Dict[str, Any]],
    platform: str,
    *,
    max_items: int = 5,
) -> str:
    if not codecs:
        return _code("none", platform)
    labels = [_codec_label(codec) for codec in codecs]
    rendered = ", ".join(_code(label, platform) for label in labels[:max_items])
    remaining = len(labels) - max_items
    if remaining > 0:
        rendered += " " + _text(f"and {remaining} more", platform)
    return rendered


def _call_state_summary(calls: List[Dict[str, Any]], platform: str) -> str:
    if not calls:
        return _code("0", platform)
    counts: Dict[str, int] = {}
    for call in calls:
        state = str(call.get("state") or "UNKNOWN")
        counts[state] = counts.get(state, 0) + 1
    parts = [_code(str(len(calls)), platform)]
    parts.extend(
        f"{_code(state, platform)} {_text('x', platform)} {_code(count, platform)}"
        for state, count in sorted(counts.items())
    )
    return _text(" | ", platform).join(parts)


def report(
    source: Optional[Any] = None,
    *,
    platform: str = "discord",
    media_type: Optional[str] = "audio",
) -> str:
    """Render concise emoji telemetry using Discord or Telegram Markdown.

    ``platform='discord'`` emits Discord Markdown. ``platform='telegram'`` emits
    Telegram MarkdownV2-safe output. Both formats avoid large Markdown headers.
    """
    platform = str(platform or "discord").lower()
    if platform not in ("discord", "telegram", "telegram-v2", "telegram_markdown_v2"):
        raise ValueError("platform must be 'discord' or 'telegram'.")
    if platform.startswith("telegram"):
        platform = "telegram"

    data = snapshot(source, media_type=media_type)
    lines = [_bold("📡 pyVoIP telemetry", platform)]

    phone = data.get("phone") or {}
    sip = data.get("sip") or {}
    if phone:
        status = phone.get("status")
        target = None
        if isinstance(sip, dict):
            target_data = sip.get("target") or {}
            if target_data:
                target = f"{sip.get('transport') or 'SIP'} {target_data.get('host')}:{target_data.get('port')}"
        line = f"📞 {_text('Phone', platform)}: {_code(status, platform)}"
        if target:
            line += _text(" | ", platform) + f"{_text('SIP', platform)} {_code(target, platform)}"
        lines.append(line)
    elif isinstance(sip, dict) and sip:
        target_data = sip.get("target") or {}
        target = None
        if target_data:
            target = f"{sip.get('transport') or 'SIP'} {target_data.get('host')}:{target_data.get('port')}"
        line = f"📡 {_text('SIP', platform)}: {_code('running' if sip.get('running') else 'stopped', platform)}"
        if target:
            line += _text(" | ", platform) + _code(target, platform)
        lines.append(line)

    auth = data.get("auth") or (sip.get("auth") if isinstance(sip, dict) else {}) or {}
    last_digest = auth.get("last_digest") if isinstance(auth, dict) else None
    if last_digest:
        auth_bits = [
            _code(last_digest.get("algorithm"), platform),
            f"{_text('qop', platform)} {_code(last_digest.get('qop') or 'none', platform)}",
        ]
        if last_digest.get("header"):
            auth_bits.append(f"{_text('via', platform)} {_code(last_digest.get('header'), platform)}")
        lines.append(f"🔐 {_text('Auth', platform)}: " + _text(" | ", platform).join(auth_bits))
    else:
        challenges = auth.get("challenges") if isinstance(auth, dict) else None
        if challenges:
            offered = sorted({str(item.get("algorithm")) for item in challenges if item.get("algorithm")})
            lines.append(
                f"🔐 {_text('Auth challenge', platform)}: "
                + ", ".join(_code(item, platform) for item in offered[:4])
            )
        else:
            lines.append(f"🔐 {_text('Auth', platform)}: {_code('not used yet', platform)}")

    codecs = data.get("codecs") or {}
    if isinstance(codecs, dict):
        active = codecs.get("active_codecs") or codecs.get("active") or []
        if active:
            lines.append(
                f"🎙️ {_text('Active codec', platform)}: "
                + _codec_summary(active, platform, max_items=3)
            )

        local_offer = codecs.get("local_offer") or []
        if local_offer:
            lines.append(
                f"🎧 {_text('Local offer', platform)}: "
                + _codec_summary(local_offer, platform, max_items=5)
            )

        remote = codecs.get("remote") or []
        if remote:
            lines.append(
                f"📨 {_text('Remote SDP', platform)}: "
                + _codec_summary(remote, platform, max_items=5)
            )

        if "can_start_call" in codecs:
            state = codecs.get("can_start_call")
            if state is True:
                rendered = "yes"
            elif state is False:
                rendered = "no"
            else:
                rendered = "unknown"
            lines.append(f"✅ {_text('Can start call', platform)}: {_code(rendered, platform)}")

    calls = data.get("calls")
    if isinstance(calls, list):
        lines.append(f"☎️ {_text('Calls', platform)}: {_call_state_summary(calls, platform)}")

    return "\n".join(lines)


def discord_report(source: Optional[Any] = None, *, media_type: Optional[str] = "audio") -> str:
    """Render a Discord Markdown telemetry report."""
    return report(source, platform="discord", media_type=media_type)


def telegram_report(source: Optional[Any] = None, *, media_type: Optional[str] = "audio") -> str:
    """Render a Telegram MarkdownV2 telemetry report."""
    return report(source, platform="telegram", media_type=media_type)
