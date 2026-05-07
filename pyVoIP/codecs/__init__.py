from typing import Dict, List, Optional, Type

from pyVoIP.RTP import PayloadType
from pyVoIP.codecs.base import CodecAvailability, RTPCodec
from pyVoIP.codecs.g711 import PCMACodec, PCMUCodec
from pyVoIP.codecs.opus import OpusCodec


_CODEC_CLASSES: Dict[PayloadType, Type[RTPCodec]] = {
    PayloadType.OPUS: OpusCodec,
    PayloadType.PCMU: PCMUCodec,
    PayloadType.PCMA: PCMACodec,
}

_CODEC_ORDER = (
    PayloadType.OPUS,
    PayloadType.PCMU,
    PayloadType.PCMA,
)


def codec_class(payload_type: PayloadType) -> Optional[Type[RTPCodec]]:
    return _CODEC_CLASSES.get(payload_type)


def known_payload_types(*, include_events: bool = True) -> List[PayloadType]:
    payload_types = list(_CODEC_ORDER)
    if include_events:
        payload_types.append(PayloadType.EVENT)
    return payload_types


def refresh_codec_availability() -> None:
    OpusCodec.refresh_availability_cache()


def codec_availability(payload_type: PayloadType) -> Dict[str, object]:
    if payload_type == PayloadType.EVENT:
        return {
            "available": True,
            "reason": "telephone-event is built in",
            "library": None,
            "name": "telephone-event",
            "description": "telephone-event",
            "payload_kind": "event",
            "rate": 8000,
            "channels": 0,
            "can_transmit_audio": False,
            "default_payload_type": 101,
            "is_dynamic": True,
        }

    cls = codec_class(payload_type)
    if cls is None:
        return {
            "available": False,
            "reason": "No codec implementation registered",
            "library": None,
            "name": str(payload_type),
            "description": getattr(payload_type, "description", None),
            "payload_kind": "unknown",
            "rate": getattr(payload_type, "rate", 0),
            "channels": getattr(payload_type, "channel", 0),
            "can_transmit_audio": False,
            "default_payload_type": None,
            "is_dynamic": True,
        }

    availability = cls.availability()
    data = availability.as_dict()
    data.update(
        {
            "name": cls.name,
            "description": cls.description,
            "payload_kind": cls.payload_kind,
            "rate": cls.rate,
            "channels": cls.channels,
            "can_transmit_audio": bool(
                cls.can_transmit_audio and availability.available
            ),
            "default_payload_type": cls.default_payload_type,
            "is_dynamic": bool(cls.dynamic),
        }
    )
    return data


def availability_report(
    *,
    refresh: bool = False,
    include_events: bool = True,
) -> List[Dict[str, object]]:
    if refresh:
        refresh_codec_availability()
    return [
        codec_availability(payload_type)
        for payload_type in known_payload_types(include_events=include_events)
    ]


def enabled_payload_types(*, include_events: bool = True) -> List[PayloadType]:
    enabled: List[PayloadType] = []
    for payload_type in _CODEC_ORDER:
        cls = codec_class(payload_type)
        if cls is None:
            continue
        if cls.availability().available:
            enabled.append(payload_type)

    if include_events:
        enabled.append(PayloadType.EVENT)
    return enabled


def codec_can_transmit_audio(payload_type: PayloadType) -> bool:
    cls = codec_class(payload_type)
    if cls is None:
        return False
    return bool(cls.can_transmit_audio and cls.availability().available)


def default_payload_type(payload_type: PayloadType) -> Optional[int]:
    if payload_type == PayloadType.EVENT:
        return 101

    cls = codec_class(payload_type)
    if cls is not None:
        return cls.default_payload_type

    try:
        return int(payload_type)
    except Exception:
        return None


def create_codec(payload_type: PayloadType) -> Optional[RTPCodec]:
    cls = codec_class(payload_type)
    if cls is None:
        return None

    availability = cls.availability()
    if not availability.available:
        raise RuntimeError(
            f"{payload_type} codec unavailable: {availability.reason}"
        )
    return cls()


def rtpmap_for_codec(payload_type: PayloadType, negotiated_payload: int) -> str:
    if payload_type == PayloadType.EVENT:
        return f"{negotiated_payload} telephone-event/8000"

    cls = codec_class(payload_type)
    if cls is not None:
        return cls.rtpmap(negotiated_payload)
    return ""


def fmtp_for_codec(payload_type: PayloadType) -> List[str]:
    if payload_type == PayloadType.EVENT:
        return ["0-15"]

    cls = codec_class(payload_type)
    if cls is not None:
        return cls.fmtp()
    return []
