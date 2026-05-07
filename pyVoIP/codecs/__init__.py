from typing import Dict, List, Optional, Type

from pyVoIP.RTP import PayloadType
from pyVoIP.codecs.base import CodecAvailability, RTPCodec
from pyVoIP.codecs.g711 import PCMACodec, PCMAWBCodec, PCMUCodec, PCMUWBCodec
from pyVoIP.codecs.opus import OpusCodec
from pyVoIP.codecs.silk import (
    Silk8000Codec,
    Silk12000Codec,
    Silk16000Codec,
    Silk24000Codec,
    SilkCodec,
)


_CODEC_CLASSES: Dict[PayloadType, Type[RTPCodec]] = {
    PayloadType.OPUS: OpusCodec,
    PayloadType.SILK_24000: Silk24000Codec,
    PayloadType.SILK_16000: Silk16000Codec,
    PayloadType.SILK_12000: Silk12000Codec,
    PayloadType.SILK_8000: Silk8000Codec,
    PayloadType.PCMU_WB: PCMUWBCodec,
    PayloadType.PCMA_WB: PCMAWBCodec,
    PayloadType.PCMU: PCMUCodec,
    PayloadType.PCMA: PCMACodec,
}

_CODEC_ORDER = (
    PayloadType.OPUS,
    PayloadType.SILK_24000,
    PayloadType.SILK_16000,
    PayloadType.SILK_12000,
    PayloadType.SILK_8000,
    PayloadType.PCMU_WB,
    PayloadType.PCMA_WB,
    PayloadType.PCMU,
    PayloadType.PCMA,
)

_CODEC_ORDER_INDEX = {
    payload_type: index for index, payload_type in enumerate(_CODEC_ORDER)
}
_CODEC_PRIORITY_OVERRIDES: Dict[PayloadType, int] = {}


def _normalize_payload_type(payload_type) -> PayloadType:
    if isinstance(payload_type, PayloadType):
        return payload_type

    try:
        return PayloadType(int(payload_type))
    except Exception:
        pass

    needle = str(payload_type or "").strip().lower()
    for codec in PayloadType:
        names = {
            codec.name.lower(),
            str(codec).lower(),
            codec.description.lower(),
            str(codec.value).lower(),
        }
        if needle in names:
            return codec

    raise ValueError(f"RTP payload type {payload_type!r} not found.")


def codec_class(payload_type: PayloadType) -> Optional[Type[RTPCodec]]:
    return _CODEC_CLASSES.get(_normalize_payload_type(payload_type))


def codec_priority_score(payload_type: PayloadType) -> int:
    payload_type = _normalize_payload_type(payload_type)
    if payload_type in _CODEC_PRIORITY_OVERRIDES:
        return _CODEC_PRIORITY_OVERRIDES[payload_type]
    if payload_type == PayloadType.EVENT:
        return -1000

    cls = codec_class(payload_type)
    if cls is None:
        return 0
    return int(getattr(cls, "priority_score", 0))


def set_codec_priority(payload_type: PayloadType, score: int) -> None:
    _CODEC_PRIORITY_OVERRIDES[_normalize_payload_type(payload_type)] = int(score)


def reset_codec_priorities() -> None:
    _CODEC_PRIORITY_OVERRIDES.clear()


def codec_priorities(*, include_events: bool = True) -> Dict[PayloadType, int]:
    return {
        payload_type: codec_priority_score(payload_type)
        for payload_type in known_payload_types(include_events=include_events)
    }


def sorted_payload_types(payload_types: List[PayloadType]) -> List[PayloadType]:
    indexed = list(enumerate(payload_types))
    indexed.sort(
        key=lambda item: (
            -codec_priority_score(item[1]),
            _CODEC_ORDER_INDEX.get(item[1], 999),
            item[0],
        )
    )
    return [payload_type for _index, payload_type in indexed]


def known_payload_types(*, include_events: bool = True) -> List[PayloadType]:
    payload_types = sorted_payload_types(list(_CODEC_ORDER))
    if include_events:
        payload_types.append(PayloadType.EVENT)
    return payload_types


def refresh_codec_availability() -> None:
    OpusCodec.refresh_availability_cache()
    SilkCodec.refresh_availability_cache()


def codec_availability(payload_type: PayloadType) -> Dict[str, object]:
    payload_type = _normalize_payload_type(payload_type)

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
            "priority_score": codec_priority_score(payload_type),
            "preferred_source_sample_rate": None,
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
            "priority_score": 0,
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
            "priority_score": codec_priority_score(payload_type),
            "preferred_source_sample_rate": getattr(
                cls,
                "preferred_source_sample_rate",
                getattr(cls, "source_sample_rate", 8000),
            ),
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

    enabled = sorted_payload_types(enabled)
    if include_events:
        enabled.append(PayloadType.EVENT)
    return enabled


def codec_fmtp_supported(payload_type: PayloadType, fmtp: List[str]) -> bool:
    payload_type = _normalize_payload_type(payload_type)
    if payload_type == PayloadType.EVENT:
        return True

    cls = codec_class(payload_type)
    if cls is None:
        return False
    return bool(cls.fmtp_supported(list(fmtp or [])))


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


def create_codec(
    payload_type: PayloadType,
    *,
    source_sample_rate: Optional[int] = None,
    source_sample_width: int = 1,
    source_channels: int = 1,
) -> Optional[RTPCodec]:
    cls = codec_class(payload_type)
    if cls is None:
        return None

    availability = cls.availability()
    if not availability.available:
        raise RuntimeError(
            f"{payload_type} codec unavailable: {availability.reason}"
        )

    adapter = cls()
    adapter.configure_source_format(
        sample_rate=source_sample_rate,
        sample_width=source_sample_width,
        channels=source_channels,
    )
    return adapter


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
