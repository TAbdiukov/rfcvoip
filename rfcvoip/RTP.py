from enum import Enum
from threading import Thread
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union
from collections import deque
import audioop
import errno
import io
import ipaddress
import math
import rfcvoip
import random
import socket
import threading
import time
import warnings

from rfcvoip.audio_format import (
    PublicAudioFormat,
    normalize_audio_bit_depth,
    preferred_public_bit_depth,
    sample_width_bytes,
)


__all__ = [
    "add_bytes",
    "byte_to_bits",
    "DTMF_DIGITS",
    "DynamicPayloadType",
    "codec_fmtp_supported",
    "codec_priority_score",
    "default_payload_type",
    "fmtp_for_payload_type",
    "is_audio_codec",
    "is_transmittable_audio_codec",
    "is_video_codec",
    "normalize_dtmf_digit",
    "normalize_dtmf_sequence",
    "payload_type_from_name",
    "payload_type_media_kind",
    "prioritize_payload_type_map",
    "rtpmap_for_payload_type",
    "reset_codec_priorities",
    "set_codec_priority",
    "PayloadType",
    "select_transmittable_audio_codec",
    "RTPParseError",
    "UnsupportedCodecError",
    "RTPProtocol",
    "RTPPacketManager",
    "RTPAudioOutput",
    "RTPClient",
    "TransmitType",
]

debug = rfcvoip.debug

_DTMF_EVENT_TO_CHAR = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#", "A", "B", "C", "D",
]
_DTMF_CHAR_TO_EVENT = {char: idx for idx, char in enumerate(_DTMF_EVENT_TO_CHAR)}
DTMF_DIGITS = "".join(_DTMF_EVENT_TO_CHAR)
_DTMF_BASIC_DIGITS = frozenset("0123456789*#")
_DTMF_EXTENDED_DIGITS = frozenset(DTMF_DIGITS)


def normalize_dtmf_sequence(
    value: str,
    *,
    allow_abcd: bool = True,
) -> str:
    """Return an uppercase DTMF sequence, or ``""`` when invalid.

    Whitespace is ignored so command frontends may pass values such as
    ``"1 2 #"``.  Any non-DTMF character rejects the entire sequence.
    """
    allowed = _DTMF_EXTENDED_DIGITS if allow_abcd else _DTMF_BASIC_DIGITS
    cleaned = []
    for char in str(value or "").upper():
        if char.isspace():
            continue
        if char not in allowed:
            return ""
        cleaned.append(char)
    return "".join(cleaned)


def normalize_dtmf_digit(
    value: str,
    *,
    allow_abcd: bool = True,
) -> str:
    """Return one normalized DTMF digit, or ``""`` when invalid."""
    digit = normalize_dtmf_sequence(value, allow_abcd=allow_abcd)
    return digit if len(digit) == 1 else ""


def byte_to_bits(byte: bytes) -> str:
    if len(byte) != 1:
        raise ValueError(f"byte_to_bits expects 1 byte, got {len(byte)}")
    return format(byte[0], "08b")


def add_bytes(byte_string: bytes) -> int:
    return int.from_bytes(byte_string, "big", signed=False)


class DynamicPayloadType(Exception):
    pass


class RTPParseError(Exception):
    pass


class UnsupportedCodecError(RTPParseError):
    """Raised when an RTP payload maps to a codec rfcvoip cannot handle."""

    def __init__(
        self,
        codec: Any = None,
        *,
        payload_type: Optional[int] = None,
        operation: str = "process",
        reason: str = "",
    ):
        self.codec = codec
        self.payload_type = payload_type
        self.operation = operation
        self.reason = str(reason or "")

        codec_name = str(codec) if codec is not None else "unknown"
        message = f"Unable to {operation} RTP codec {codec_name}"
        if payload_type is not None:
            message += f" (payload type {payload_type})"
        if self.reason:
            message += f": {self.reason}"
        super().__init__(message)


class RTPProtocol(Enum):
    UDP = "udp"
    AVP = "RTP/AVP"
    SAVP = "RTP/SAVP"


class TransmitType(Enum):
    RECVONLY = "recvonly"
    SENDRECV = "sendrecv"
    SENDONLY = "sendonly"
    INACTIVE = "inactive"

    def __str__(self):
        return self.value


class PayloadType(Enum):
    def __new__(
        cls,
        value: Union[int, str],
        clock: int = 0,
        channel: int = 0,
        description: str = "",
    ):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.rate = clock
        obj.channel = channel
        obj.description = description
        return obj

    @property
    def rate(self) -> int:
        return self._rate

    @rate.setter
    def rate(self, value: int) -> None:
        self._rate = value

    @property
    def channel(self) -> int:
        return self._channel

    @channel.setter
    def channel(self, value: int) -> None:
        self._channel = value

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value: str) -> None:
        self._description = value

    def __int__(self) -> int:
        try:
            return int(self.value)
        except ValueError:
            pass
        raise DynamicPayloadType(
            self.description + " is a dynamically assigned payload"
        )

    def __str__(self) -> str:
        if isinstance(self.value, int):
            return self.description
        return str(self.value)

    # Audio
    PCMU = 0, 8000, 1, "PCMU"
    GSM = 3, 8000, 1, "GSM"
    G723 = 4, 8000, 1, "G723"
    DVI4_8000 = 5, 8000, 1, "DVI4"
    DVI4_16000 = 6, 16000, 1, "DVI4"
    LPC = 7, 8000, 1, "LPC"
    PCMA = 8, 8000, 1, "PCMA"
    PCMU_WB = "PCMU-WB", 16000, 1, "PCMU-WB"
    PCMA_WB = "PCMA-WB", 16000, 1, "PCMA-WB"
    G722 = 9, 8000, 1, "G722"
    L16_2 = 10, 44100, 2, "L16"
    L16 = 11, 44100, 1, "L16"
    QCELP = 12, 8000, 1, "QCELP"
    CN = 13, 8000, 1, "CN"
    # MPA channel varries, should be defined in the RTP packet.
    MPA = 14, 90000, 0, "MPA"
    G728 = 15, 8000, 1, "G728"
    DVI4_11025 = 16, 11025, 1, "DVI4"
    DVI4_22050 = 17, 22050, 1, "DVI4"
    G729 = 18, 8000, 1, "G729"
    OPUS = "opus", 48000, 1, "opus"
    SILK_24000 = "SILK/24000", 24000, 1, "SILK"
    SILK_16000 = "SILK/16000", 16000, 1, "SILK"
    SILK_12000 = "SILK/12000", 12000, 1, "SILK"
    SILK_8000 = "SILK/8000", 8000, 1, "SILK"

    # Video
    CELB = 25, 90000, 0, "CelB"
    JPEG = 26, 90000, 0, "JPEG"
    NV = 28, 90000, 0, "nv"
    H261 = 31, 90000, 0, "H261"
    MPV = 32, 90000, 0, "MPV"
    # MP2T is both audio and video per RFC 3551 July 2003 5.7
    MP2T = 33, 90000, 1, "MP2T"
    H263 = 34, 90000, 0, "H263"

    # Non-codec
    EVENT = "telephone-event", 8000, 0, "telephone-event"
    UNKNOWN = "UNKNOWN", 0, 0, "UNKNOWN CODEC"

_AUDIO_PAYLOAD_TYPES = frozenset(
    (
        PayloadType.PCMU,
        PayloadType.GSM,
        PayloadType.G723,
        PayloadType.DVI4_8000,
        PayloadType.DVI4_16000,
        PayloadType.LPC,
        PayloadType.PCMA,
        PayloadType.PCMU_WB,
        PayloadType.PCMA_WB,
        PayloadType.G722,
        PayloadType.L16_2,
        PayloadType.L16,
        PayloadType.QCELP,
        PayloadType.CN,
        PayloadType.MPA,
        PayloadType.G728,
        PayloadType.DVI4_11025,
        PayloadType.DVI4_22050,
        PayloadType.G729,
        PayloadType.OPUS,
        PayloadType.SILK_24000,
        PayloadType.SILK_16000,
        PayloadType.SILK_12000,
        PayloadType.SILK_8000,
        # RFC 3551 defines MP2T as both audio and video.  It is classified as
        # audio here for reporting, but it is intentionally not transmittable
        # because rfcvoip does not implement an MP2T encoder.
        PayloadType.MP2T,
    )
)

_VIDEO_PAYLOAD_TYPES = frozenset(
    (
        PayloadType.CELB,
        PayloadType.JPEG,
        PayloadType.NV,
        PayloadType.H261,
        PayloadType.MPV,
        PayloadType.MP2T,
        PayloadType.H263,
    )
)

def payload_type_media_kind(codec: PayloadType) -> str:
    """Return rfcvoip's broad media classification for an RTP payload type.

    This is deliberately separate from SDP media sections.  A payload such as
    ``telephone-event`` appears inside an ``m=audio`` section, but it is not an
    audio codec that can be selected for the continuous RTP media stream.
    """
    if codec == PayloadType.EVENT:
        return "event"
    if codec == PayloadType.UNKNOWN:
        return "unknown"
    if codec == PayloadType.MP2T:
        return "audio/video"

    try:
        from rfcvoip.codecs import codec_payload_kind

        payload_kind = codec_payload_kind(codec)
        if payload_kind:
            return payload_kind
    except Exception:
        pass

    if codec in _AUDIO_PAYLOAD_TYPES:
        return "audio"
    if codec in _VIDEO_PAYLOAD_TYPES:
        return "video"
    return "unknown"


def is_audio_codec(codec: PayloadType) -> bool:
    """Return whether ``codec`` represents an RTP audio payload type."""
    return payload_type_media_kind(codec) in ("audio", "audio/video")


def is_video_codec(codec: PayloadType) -> bool:
    """Return whether ``codec`` represents an RTP video payload type."""
    return payload_type_media_kind(codec) in ("video", "audio/video")


def codec_priority_score(
    codec: PayloadType,
    priority_scores: Optional[Dict[Any, int]] = None,
) -> int:
    """Return the local preference score for ``codec``.

    Larger scores are preferred when building local SDP offers and when
    selecting a negotiated RTP audio codec. Scores are intentionally separate
    from SDP payload numbers so deployments can tune codec preference without
    changing the wire-level payload mapping.
    """
    if priority_scores is not None:
        for key in (
            codec,
            getattr(codec, "name", None),
            str(codec),
            getattr(codec, "description", None),
            getattr(codec, "value", None),
        ):
            if key is None:
                continue
            try:
                if key in priority_scores:
                    return int(priority_scores[key])
            except (TypeError, ValueError):
                continue

    try:
        from rfcvoip.codecs import codec_priority_score as _priority_score

        return _priority_score(codec)
    except Exception:
        return 0


def set_codec_priority(codec: PayloadType, score: int) -> None:
    """Override a codec priority score for this process."""
    from rfcvoip.codecs import set_codec_priority as _set_codec_priority

    _set_codec_priority(codec, score)
    refresh = getattr(rfcvoip, "refresh_supported_codecs", None)
    if callable(refresh):
        refresh()


def reset_codec_priorities() -> None:
    """Reset all codec priority overrides to their defaults."""
    from rfcvoip.codecs import reset_codec_priorities as _reset_codec_priorities

    _reset_codec_priorities()
    refresh = getattr(rfcvoip, "refresh_supported_codecs", None)
    if callable(refresh):
        refresh()


def prioritize_payload_type_map(
    assoc: Dict[int, PayloadType],
    priority_scores: Optional[Dict[Any, int]] = None,
) -> Dict[int, PayloadType]:
    """Return ``assoc`` ordered by rfcvoip codec priority.

    Ties preserve the input order, which keeps remote SDP order meaningful when
    local scores are equal.
    """
    indexed = list(enumerate(assoc.items()))
    indexed.sort(
        key=lambda item: (
            -codec_priority_score(
                item[1][1],
                priority_scores=priority_scores,
            ),
            item[0],
        )
    )
    return {payload_type: codec for _idx, (payload_type, codec) in indexed}


def codec_fmtp_supported(
    codec: PayloadType,
    fmtp: Optional[List[str]] = None,
) -> bool:
    """Return whether rfcvoip can satisfy negotiated FMTP constraints."""
    try:
        from rfcvoip.codecs import codec_fmtp_supported as _codec_fmtp_supported

        return _codec_fmtp_supported(codec, fmtp or [])
    except Exception:
        return True


def default_payload_type(codec: PayloadType) -> Optional[int]:
    try:
        from rfcvoip.codecs import default_payload_type as _default_payload_type

        return _default_payload_type(codec)
    except Exception:
        try:
            return int(codec)
        except Exception:
            return None


def rtpmap_for_payload_type(payload_type: int, codec: PayloadType) -> str:
    try:
        from rfcvoip.codecs import rtpmap_for_codec

        rtpmap = rtpmap_for_codec(codec, int(payload_type))
        if rtpmap:
            return rtpmap
    except Exception:
        pass

    channels = codec.channel
    channel_suffix = f"/{channels}" if channels and channels > 1 else ""
    return f"{payload_type} {codec}/{codec.rate}{channel_suffix}"


def fmtp_for_payload_type(payload_type: int, codec: PayloadType) -> List[str]:
    try:
        from rfcvoip.codecs import fmtp_for_codec

        return fmtp_for_codec(codec)
    except Exception:
        if codec == PayloadType.EVENT:
            return ["0-15"]
        return []


def _codec_unavailable_reason(codec: PayloadType) -> str:
    try:
        from rfcvoip.codecs import codec_availability

        availability = codec_availability(codec)
    except Exception as ex:
        return str(ex) or "codec availability could not be determined"

    reason = str(availability.get("reason") or "").strip()
    if not reason:
        reason = "no codec implementation registered"

    install_extras = availability.get("install_extras") or []
    if isinstance(install_extras, str):
        install_extras = [install_extras]
    install_extras = [
        str(extra).strip()
        for extra in install_extras
        if str(extra).strip()
    ]
    if install_extras:
        reason += " Install one of: " + ", ".join(install_extras) + "."

    return reason


def is_transmittable_audio_codec(
    codec: PayloadType,
    enabled_codecs: Optional[Any] = None,
) -> bool:
    """Return whether rfcvoip can encode ``codec`` as the main audio stream."""
    compatible_codecs = (
        getattr(rfcvoip, "RTPCompatibleCodecs", ())
        if enabled_codecs is None
        else enabled_codecs
    )
    if codec not in compatible_codecs:  # pragma: no branch
        return False

    try:
        from rfcvoip.codecs import codec_can_transmit_audio

        return codec_can_transmit_audio(codec)
    except Exception:
        return False


def select_transmittable_audio_codec(
    assoc: Dict[int, PayloadType],
    priority_scores: Optional[Dict[Any, int]] = None,
    enabled_codecs: Optional[Any] = None,
) -> Tuple[int, PayloadType]:
    """Select the negotiated payload number and codec used for RTP audio.

    ``assoc`` maps negotiated RTP payload numbers to :class:`PayloadType`
    values.  Returning the negotiated payload number is important when a peer
    maps a known codec, such as PCMU, to a dynamic payload number via SDP
    ``rtpmap``.
    """
    rejected = []
    candidates: List[Tuple[int, int, PayloadType]] = []
    for index, (payload_type, codec) in enumerate(assoc.items()):
        try:
            payload_number = int(payload_type)
        except (TypeError, ValueError):
            rejected.append(f"{payload_type}:{codec} (invalid payload number)")
            continue
        if not 0 <= payload_number <= 127:
            rejected.append(f"{payload_type}:{codec} (invalid payload number)")
            continue

        if is_transmittable_audio_codec(
            codec,
            enabled_codecs=enabled_codecs,
        ):
            candidates.append((index, payload_number, codec))
            continue

        rejected.append(
            f"{payload_number}:{codec} ({payload_type_media_kind(codec)})"
        )

    if candidates:
        _index, payload_number, codec = sorted(
            candidates,
            key=lambda item: (
                -codec_priority_score(
                    item[2],
                    priority_scores=priority_scores,
                ),
                item[0],
            ),
        )[0]
        return payload_number, codec

    detail = ": " + ", ".join(rejected) if rejected else "."
    raise RTPParseError("No transmittable audio codec negotiated" + detail)


def _rtpmap_channel_count(codec: PayloadType) -> int:
    try:
        from rfcvoip.codecs import codec_class

        cls = codec_class(codec)
        channels = (
            getattr(cls, "rtpmap_channels", None)
            if cls is not None
            else None
        )
        if channels not in (None, 0, ""):
            return int(channels)
    except Exception:
        pass

    return int(getattr(codec, "channel", 0) or 0)


def payload_type_from_name(
    name: str,
    rate: Optional[Union[int, str]] = None,
    channels: Optional[Union[int, str]] = None,
) -> PayloadType:
    """Return a :class:`PayloadType` matching an SDP codec name.

    SDP ``rtpmap`` lines identify dynamic payloads by encoding names such as
    ``telephone-event`` or ``opus``.  Static payloads are often represented by
    their payload number, but some peers still include an ``rtpmap`` name like
    ``PCMU``.  ``rate`` and ``channels`` disambiguate families with the same
    encoding name, such as ``DVI4`` and ``SILK``.
    """
    needle = str(name or "").strip().lower()
    if not needle:
        raise ValueError("Codec name cannot be empty.")

    desired_rate = None
    if rate not in (None, ""):
        try:
            desired_rate = int(rate)
        except (TypeError, ValueError):
            desired_rate = None

    desired_channels = None
    if channels not in (None, ""):
        try:
            desired_channels = int(channels)
        except (TypeError, ValueError):
            desired_channels = None

    matches = []
    for codec in PayloadType:
        names = {str(codec).lower(), codec.description.lower()}
        if isinstance(codec.value, str):
            names.add(codec.value.lower())
        if needle in names:
            matches.append(codec)

    if not matches:
        raise ValueError(f"RTP Payload type {name!r} not found.")

    if desired_rate is not None:
        rate_matches = [
            codec for codec in matches if int(getattr(codec, "rate", 0)) == desired_rate
        ]
        if rate_matches:
            matches = rate_matches
        else:
            raise ValueError(
                f"RTP Payload type {name!r}/{desired_rate} not found."
            )

    if desired_channels is not None:
        channel_matches = [
            codec
            for codec in matches
            if _rtpmap_channel_count(codec) == desired_channels
        ]
        if channel_matches:
            matches = channel_matches
        else:
            raise ValueError(
                f"RTP Payload type {name!r} with {desired_channels} "
                + "channels not found."
            )

    return matches[0]


class RTPPacketManager:
    def __init__(self, silence_byte: bytes = b"\x80"):
        self.offset = 4294967296
        """
        The largest number storable in 4 bytes + 1. This will ensure the
        offset adjustment in self.write(offset, data) works.
        """
        self.buffer = io.BytesIO()
        self.bufferLock = threading.RLock()
        self.log = {}
        self.max_log_span = 200000
        self.rebuilding = False
        self._initialized = False
        self._read_started = False
        self.silence_byte = bytes(silence_byte or b"\x00")[:1] or b"\x00"

    def _available_locked(self) -> int:
        bufferloc = self.buffer.tell()
        self.buffer.seek(0, 2)
        end = self.buffer.tell()
        self.buffer.seek(bufferloc, 0)
        return max(0, end - bufferloc)

    def available(self) -> int:
        with self.bufferLock:
            return self._available_locked()

    def _compact_locked(self) -> None:
        """Discard consumed bytes without moving the absolute playout cursor."""
        consumed = self.buffer.tell()
        if not consumed or not self._initialized:
            return
        self.buffer = io.BytesIO(self.buffer.read())
        self.offset += consumed
        self.log = {
            offset: payload
            for offset, payload in self.log.items()
            if offset + len(payload) > self.offset
        }

    def read(self, length: int = 160) -> bytes:
        if length < 0:
            raise ValueError("RTP audio read length must not be negative.")
        with self.bufferLock:
            packet = self.buffer.read(length)
            if len(packet) < length:
                missing = length - len(packet)
                self.buffer.seek(missing, 1)
                packet = packet + (self.silence_byte * missing)
            if length and self._initialized:
                self._read_started = True
            if self.buffer.tell() >= self.max_log_span // 2:
                self._compact_locked()
        return packet

    def rebuild(self, reset: bool, offset: int = 0, data: bytes = b"") -> None:
        self.rebuilding = True
        try:
            with self.bufferLock:
                if reset:
                    self.offset = offset
                    self.log = {offset: data}
                    self.buffer = io.BytesIO(data)
                    self._initialized = True
                    self._read_started = False
                    return

                bufferloc = self.buffer.tell()
                old_log = sorted(list(self.log.items()))
                self.buffer = io.BytesIO()
                for pkt, payload in old_log:
                    adjusted_offset = pkt - self.offset
                    if adjusted_offset < 0:
                        payload = payload[-adjusted_offset:]
                        adjusted_offset = 0
                        if not payload:
                            continue
                    self.buffer.seek(0, 2)
                    buffer_end = self.buffer.tell()
                    if adjusted_offset > buffer_end:
                        self.buffer.write(
                            self.silence_byte * (adjusted_offset - buffer_end)
                        )
                    self.buffer.seek(adjusted_offset, 0)
                    self.buffer.write(payload)

                self.buffer.seek(bufferloc, 0)
        finally:
            self.rebuilding = False

    def write(self, offset: int, data: bytes) -> None:
        if not data:
            return
        with self.bufferLock:
            if not self._initialized:
                self.rebuild(True, offset, data)
                return

            # Once playout has started, late bytes must not rewind it or be
            # replayed after a rebuild. An overlapping packet can still fill
            # its unplayed tail.
            if self._read_started:
                cursor = self.offset + self.buffer.tell()
                if offset < cursor:
                    data = data[cursor - offset:]
                    offset = cursor
                    if not data:
                        return

            newest = max(max(self.log) if self.log else offset, offset)
            cutoff = newest - self.max_log_span
            if offset < cutoff:
                return

            # Keep the visible byte intervals, rather than whole historical
            # packets. Replaying overlaps by timestamp can otherwise restore
            # stale bytes or lose the untouched tail of a shorter overwrite.
            end = offset + len(data)
            for logged_offset, logged_data in list(self.log.items()):
                logged_end = logged_offset + len(logged_data)
                if logged_offset < cutoff:
                    del self.log[logged_offset]
                    if logged_end <= cutoff:
                        continue
                    logged_data = logged_data[cutoff - logged_offset:]
                    logged_offset = cutoff
                    self.log[logged_offset] = logged_data
                if logged_offset < end and logged_end > offset:
                    del self.log[logged_offset]
                    if logged_offset < offset:
                        self.log[logged_offset] = logged_data[
                            :offset - logged_offset
                        ]
                    if logged_end > end:
                        self.log[end] = logged_data[end - logged_offset:]
            self.log[offset] = data
            bufferloc = self.buffer.tell()
            if offset < self.offset:
                """
                If the new timestamp is over 100,000 bytes before the
                earliest, erase the buffer.  This will stop memory errors.
                """
                reset = abs(offset - self.offset) >= 100000
                self.offset = offset
                """
                Rebuilds the buffer if something before the earliest
                timestamp comes in, this will stop overwritting.
                """
                self.rebuild(reset, offset, data)
                return
            else:
                adjusted_offset = offset - self.offset
                if adjusted_offset > self.max_log_span:
                    self._compact_locked()
                    adjusted_offset = offset - self.offset
                    bufferloc = self.buffer.tell()
                if adjusted_offset > self.max_log_span:
                    self.offset = offset
                    self.log = {offset: data}
                    self.buffer = io.BytesIO(data)
                    self.buffer.seek(0, 0)
                    self._read_started = False
                    return

                self.buffer.seek(0, 2)
                buffer_end = self.buffer.tell()
                if adjusted_offset > buffer_end:
                    self.buffer.write(
                        self.silence_byte * (adjusted_offset - buffer_end)
                    )
                self.buffer.seek(adjusted_offset, 0)
                self.buffer.write(data)
                self.buffer.seek(bufferloc, 0)

class RTPMessage:
    def __init__(self, data: bytes, assoc: Dict[int, PayloadType]):
        self.RTPCompatibleVersions = rfcvoip.RTPCompatibleVersions
        self.assoc = assoc
        # Setting defaults to stop mypy from complaining
        self.version = 0
        self.padding = False
        self.extension = False
        self.CC = 0
        self.marker = False
        self.payload_type = PayloadType.UNKNOWN
        self.payload_number = 0
        self.sequence = 0
        self.timestamp = 0
        self.SSRC = 0
        self.CSRC = []
        self.extension_profile = None
        self.extension_payload = b""

        self.parse(data)

    def summary(self) -> str:
        data = ""
        data += f"Version: {self.version}\n"
        data += f"Padding: {self.padding}\n"
        data += f"Extension: {self.extension}\n"
        data += f"CC: {self.CC}\n"
        data += f"Marker: {self.marker}\n"
        data += (
            f"Payload Type: {self.payload_type} "
            + f"({self.payload_type.value})\n"
        )
        data += f"Sequence Number: {self.sequence}\n"
        data += f"Timestamp: {self.timestamp}\n"
        data += f"SSRC: {self.SSRC}\n"
        return data

    def parse(self, packet: bytes) -> None:
        if len(packet) < 12:
            raise RTPParseError(
                f"RTP packet too short: expected at least 12 bytes, got {len(packet)}"
            )

        byte = byte_to_bits(packet[0:1])
        self.version = int(byte[0:2], 2)
        if self.version not in self.RTPCompatibleVersions:
            raise RTPParseError(f"RTP Version {self.version} not compatible.")
        self.padding = bool(int(byte[2], 2))
        self.extension = bool(int(byte[3], 2))
        self.CC = int(byte[4:], 2)

        byte = byte_to_bits(packet[1:2])
        self.marker = bool(int(byte[0], 2))

        pt = int(byte[1:], 2)
        self.payload_number = pt
        if pt in self.assoc:
            self.payload_type = self.assoc[pt]
        else:
            try:
                self.payload_type = PayloadType(pt)
                e = False
            except ValueError as ex:
                raise UnsupportedCodecError(
                    payload_type=pt,
                    operation="parse",
                    reason=(
                        "payload type is not in the negotiated SDP payload "
                        + "map and is not a known static RTP payload"
                    ),
                ) from ex
            if e:
                raise RTPParseError(f"RTP Payload type {pt} not found.")

        self.sequence = add_bytes(packet[2:4])
        self.timestamp = add_bytes(packet[4:8])
        self.SSRC = add_bytes(packet[8:12])

        self.CSRC = []
        self.extension_profile = None
        self.extension_payload = b""

        i = 12
        if len(packet) < i + (self.CC * 4):
            raise RTPParseError("RTP packet truncated in CSRC list.")

        for x in range(self.CC):
            self.CSRC.append(packet[i : i + 4])
            i += 4

        if self.extension:
            if len(packet) < i + 4:
                raise RTPParseError("RTP packet truncated in extension header.")
            self.extension_profile = add_bytes(packet[i : i + 2])
            extension_length = add_bytes(packet[i + 2 : i + 4]) * 4
            i += 4
            if len(packet) < i + extension_length:
                raise RTPParseError("RTP packet truncated in extension payload.")
            self.extension_payload = packet[i : i + extension_length]
            i += extension_length

        payload = packet[i:]
        if self.padding:
            if not payload:
                raise RTPParseError("RTP packet padding flag set without payload.")
            padding_length = payload[-1]
            if padding_length == 0 or padding_length > len(payload):
                raise RTPParseError("RTP packet contains invalid padding.")
            payload = payload[:-padding_length]

        self.payload = payload


class RTPAudioOutput:
    """Sequential PCM output, independent of incoming RTP timestamps.

    The default mode preserves whole queued announcements. Live bridges can
    opt into bounded latency and startup/recovery headroom with
    :meth:`enable_realtime`.
    """

    def __init__(
        self,
        frame_bytes: int,
        silence_byte: bytes = b"\x80",
        *,
        sample_alignment: int = 1,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.bufferLock = threading.RLock()
        self._clock = clock or time.perf_counter
        self._chunks = deque()
        self._size = 0
        self._primed = False
        self._max_latency = None
        self._prefill_seconds = 0.0
        self._sample_alignment = max(1, int(sample_alignment))
        self._expired_alignment_skip = 0
        self.underruns = self.dropped_bytes = self.expired_bytes = 0
        self.output_state = "prefill"
        self.configure(frame_bytes, 0.020, silence_byte)

    def configure(
        self, frame_bytes: int, period: float, silence_byte: bytes
    ) -> None:
        with self.bufferLock:
            self.frame_bytes = max(1, int(frame_bytes))
            self.period = max(0.001, float(period))
            self.silence_byte = silence_byte
            self._prefill = max(
                1, math.ceil(self._prefill_seconds / self.period - 1e-9)
            ) * self.frame_bytes
            self._maximum = (
                None if self._max_latency is None else max(
                    self.frame_bytes,
                    int(self._max_latency / self.period + 1e-9)
                    * self.frame_bytes,
                )
            )
            if self._maximum is not None:
                self._prefill = min(self._prefill, self._maximum)
            self._trim_locked()

    def enable_realtime(
        self, *, max_latency: float = 0.140, prefill: float = 0.040
    ) -> None:
        """Bound queued live PCM by age/size, re-priming after starvation.

        Times are in seconds. Do not enable this mode for whole-file writes:
        old queued audio is deliberately dropped to keep a live call current.
        """
        max_latency, prefill = float(max_latency), float(prefill)
        if (
            not math.isfinite(max_latency)
            or not math.isfinite(prefill)
            or max_latency <= 0
            or prefill < 0
            or prefill > max_latency
        ):
            raise ValueError("Require 0 <= prefill <= max_latency and positive latency.")
        with self.bufferLock:
            self._max_latency = max_latency
            self._prefill_seconds = prefill
            self._primed = False
            self.configure(self.frame_bytes, self.period, self.silence_byte)
            self._trim_locked()

    def _take_locked(self, length: int) -> bytes:
        output = bytearray()
        while length and self._chunks:
            queued_at, chunk = self._chunks.popleft()
            count = min(length, len(chunk))
            output.extend(chunk[:count])
            self._size -= count
            length -= count
            if count < len(chunk):
                self._chunks.appendleft((queued_at, chunk[count:]))
        return bytes(output)

    def _trim_locked(self) -> None:
        if self._maximum is None or self._size <= self._maximum:
            return
        excess = self._size - self._maximum
        # Keep PCM sample/channel boundaries intact when discarding backlog.
        excess = min(self._size, (
            (excess + self._sample_alignment - 1) // self._sample_alignment
        ) * self._sample_alignment)
        remaining = excess
        while remaining and self._chunks:
            queued_at, chunk = self._chunks.popleft()
            count = min(remaining, len(chunk))
            self._size -= count
            remaining -= count
            if count < len(chunk):
                # A sliced memoryview retains its whole original allocation.
                # Copy the bounded tail so dropping a large live write also
                # releases the discarded bytes instead of keeping them alive.
                self._chunks.appendleft(
                    (queued_at, memoryview(bytes(chunk[count:])))
                )
        self.dropped_bytes += excess

    def write(self, offset: int, data: bytes) -> None:
        # Offset is retained for compatibility with RTPClient.write. It is a
        # storage counter, not a playout deadline, and cannot invalidate PCM.
        if not data:
            return
        with self.bufferLock:
            if self._expired_alignment_skip:
                skipped = min(len(data), self._expired_alignment_skip)
                data = data[skipped:]
                self._expired_alignment_skip -= skipped
                self.expired_bytes += skipped
                if not data:
                    return
            self._chunks.append((self._clock(), memoryview(bytes(data))))
            self._size += len(data)
            self._trim_locked()

    def read(self, length: int = 160) -> bytes:
        if length <= 0:
            return b""
        with self.bufferLock:
            now = self._clock()
            if self._max_latency is not None:
                expired = 0
                while (
                    self._chunks
                    and now - self._chunks[0][0] > self._max_latency
                ):
                    _, chunk = self._chunks.popleft()
                    self._size -= len(chunk)
                    expired += len(chunk)
                # Writes may split one interleaved PCM sample across chunks.
                # Expire to a complete sample boundary, including any fresh
                # suffix of that sample, so channels/bytes never shift.
                if expired % self._sample_alignment:
                    suffix = self._sample_alignment - expired % self._sample_alignment
                    discarded = len(self._take_locked(suffix))
                    expired += discarded
                    self._expired_alignment_skip += suffix - discarded
                self.expired_bytes += expired
                if not self._primed:
                    ready = self._size >= self._prefill
                    waited = bool(
                        self._chunks
                        and now - self._chunks[0][0] >= self._prefill_seconds
                    )
                    if self._size < length or not (ready or waited):
                        self.output_state = "prefill"
                        return self.silence_byte * length
                    self._primed = True
                if self._size < length:
                    self.underruns += 1
                    self._primed = False
                    self.output_state = "underrun"
                    return self.silence_byte * length
            elif self._size < length:
                self.underruns += 1
            # A writer may split an interleaved PCM sample across writes.
            # Keep an incomplete sample queued rather than inserting silence
            # into it and shifting every subsequent sample/channel.
            count = min(length, self._size)
            count -= count % self._sample_alignment
            output = self._take_locked(count)
            self.output_state = "audio" if output else "underrun"
            return output + self.silence_byte * (length - len(output))

    def available(self) -> int:
        with self.bufferLock:
            return self._size

    def snapshot(self) -> Dict[str, Any]:
        with self.bufferLock:
            return {
                "queued_ms": self._size / self.frame_bytes * self.period * 1000,
                "underruns": self.underruns,
                "dropped_bytes": self.dropped_bytes,
                "expired_bytes": self.expired_bytes,
                "output_state": self.output_state,
            }


class RTPClient:
    def __init__(
        self,
        assoc: Dict[int, PayloadType],
        inIP: str,
        inPort: int,
        outIP: str,
        outPort: int,
        sendrecv: TransmitType,
        dtmf: Optional[Callable[[str], None]] = None,
        audio_sample_rate: Optional[int] = None,
        audio_sample_width: int = 1,
        audio_bit_depth: Optional[Any] = None,
        audio_channels: Optional[int] = None,
        codec_priority_scores: Optional[Dict[Any, int]] = None,
        enabled_codecs: Optional[Any] = None,
        audio_output_mode: str = "buffered",
        codec_fmtp: Optional[Dict[int, List[str]]] = None,
        receive_assoc: Optional[Dict[int, PayloadType]] = None,
    ):
        if audio_output_mode not in ("buffered", "realtime"):
            raise RTPParseError(
                "audio_output_mode must be 'buffered' or 'realtime'."
            )
        self.audio_output_mode = audio_output_mode
        self._running = threading.Event()
        self._socket_lock = threading.RLock()
        self._workers: List[Thread] = []
        self.NSD = False
        # Example: {0: PayloadType.PCMU, 101: PayloadType.EVENT}
        self.assoc = assoc
        # SDP can assign different payload numbers in each direction. The
        # answer's map selects outgoing payloads; incoming packets continue
        # to use the payload numbers advertised in our original offer.
        self.receive_assoc = (
            assoc if receive_assoc is None else dict(receive_assoc)
        )
        try:
            self.sendrecv = (
                sendrecv
                if isinstance(sendrecv, TransmitType)
                else TransmitType(str(sendrecv))
            )
        except ValueError as ex:
            raise RTPParseError(
                f"Unsupported RTP transmit type {sendrecv!r}."
            ) from ex
        self._codec_adapters: Dict[PayloadType, Any] = {}
        self._receive_codec_adapters: Dict[PayloadType, Any] = {}
        self._receive_ssrc: Optional[int] = None
        self._receive_timestamp: Optional[int] = None
        self._receive_sequences = set()
        self._receive_sequence_order: Deque[Tuple[int, int]] = deque()
        self._receive_media_sequence: Optional[int] = None
        self._receive_media_timestamp: Optional[int] = None
        self._receive_clock_epochs: Deque[Tuple[int, int, int, int]] = deque(
            maxlen=256
        )
        self._receive_media_history: Deque[
            Tuple[int, int, int, int, bool, Optional[int]]
        ] = deque(maxlen=256)
        self._receive_event_end: Optional[int] = None
        self.codec_priority_scores = dict(codec_priority_scores or {})
        self.codec_fmtp = {
            payload_type: list(settings)
            for payload_type, settings in (codec_fmtp or {}).items()
        }
        self.enabled_codecs = (
            None if enabled_codecs is None else tuple(enabled_codecs)
        )
        debug("Selecting negotiated audio codec for transmission")
        try:
            (
                self.preference_payload_type,
                self.preference,
            ) = select_transmittable_audio_codec(
                assoc,
                priority_scores=self.codec_priority_scores,
                enabled_codecs=self.enabled_codecs,
            )
        except RTPParseError:
            debug(
                "No transmittable audio codec negotiated from assoc="
                + ",".join(f"{pt}:{codec}" for pt, codec in assoc.items())
             )
            raise
        debug(
            f"Selected {self.preference} "
            + f"as RTP payload {self.preference_payload_type}"
        )
        self.audio_sample_rate = self._resolve_audio_sample_rate(
            audio_sample_rate
        )
        self.audio_bit_depth = self._resolve_audio_bit_depth(
            audio_bit_depth,
            audio_sample_width,
        )
        self.audio_sample_width = sample_width_bytes(self.audio_bit_depth)
        self.audio_channels = self._resolve_audio_channels(audio_channels)

        self.inIP = inIP
        self.inPort = inPort
        self.outIP = outIP
        self.outPort = outPort
        self._socket_family = self._select_socket_family(inIP, outIP)

        self.dtmf = dtmf

        silence_byte = b"\x80" if self.audio_bit_depth == 8 else b"\x00"
        self.pmout = RTPAudioOutput(
            self.public_audio_format().frame_size,
            silence_byte=silence_byte,
            sample_alignment=self.audio_sample_width * self.audio_channels,
        )
        if self.audio_output_mode == "realtime":
            self.pmout.enable_realtime()
        self.pmin = RTPPacketManager(silence_byte=silence_byte)  # Received
        self.outOffset = random.randint(1, 5000)

        self.outSequence = random.randint(1, 100)
        self.outTimestamp = random.randint(1, 10000)
        self.outSSRC = random.randint(1000, 65530)
        self._telephone_event_pt = self._find_telephone_event_payload_type()
        self._pending_dtmf: Deque[str] = deque()
        self._dtmf_lock = threading.Lock()
        self._received_dtmf = set()
        self._received_dtmf_order: Deque[Tuple[int, int, int]] = deque()
        self._received_dtmf_segments: Dict[
            Tuple[int, int, int], Tuple[int, bool]
        ] = {}

    @staticmethod
    def _preferred_source_sample_rate(codec: PayloadType) -> int:
        try:
            from rfcvoip.codecs import codec_class

            cls = codec_class(codec)
            if cls is not None:
                rate = getattr(
                    cls,
                    "preferred_source_sample_rate",
                    getattr(cls, "source_sample_rate", None),
                )
                if rate:
                    return int(rate)
        except Exception:
            pass

        return int(getattr(codec, "rate", 8000) or 8000)

    @staticmethod
    def _preferred_source_channels(codec: PayloadType) -> int:
        try:
            from rfcvoip.codecs import codec_class

            cls = codec_class(codec)
            if cls is not None:
                for attr in (
                    "preferred_source_channels",
                    "source_channels",
                    "channels",
                    "rtpmap_channels",
                ):
                    channels = getattr(cls, attr, None)
                    if channels not in (None, 0, ""):
                        return int(channels)
        except Exception:
            pass

        return int(getattr(codec, "channel", 1) or 1)

    def _resolve_audio_sample_rate(
        self, audio_sample_rate: Optional[int]
    ) -> int:
        sample_rate = (
            self._preferred_source_sample_rate(self.preference)
            if audio_sample_rate is None
            else audio_sample_rate
        )
        try:
            sample_rate = int(sample_rate)
        except (TypeError, ValueError) as ex:
            raise RTPParseError("Audio sample rate must be an integer.") from ex
        if sample_rate <= 0:
            raise RTPParseError("Audio sample rate must be positive.")
        return sample_rate

    def _resolve_audio_bit_depth(
        self,
        audio_bit_depth: Optional[Any],
        audio_sample_width: int,
    ) -> int:
        if audio_bit_depth is None:
            try:
                audio_bit_depth = int(audio_sample_width) * 8
            except (TypeError, ValueError) as ex:
                raise RTPParseError(
                    "Public RTP audio format values must be integers."
                ) from ex

        try:
            requested = normalize_audio_bit_depth(audio_bit_depth)
        except ValueError as ex:
            raise RTPParseError(str(ex)) from ex

        if requested == "best":
            return preferred_public_bit_depth(self.preference, fallback=8)
        return int(requested)

    def _resolve_audio_channels(
        self,
        audio_channels: Optional[int],
    ) -> int:
        channels = (
            self._preferred_source_channels(self.preference)
            if audio_channels is None
            else audio_channels
        )
        try:
            channels = int(channels)
        except (TypeError, ValueError) as ex:
            raise RTPParseError("Audio channel count must be an integer.") from ex
        if channels <= 0:
            raise RTPParseError("Audio channel count must be positive.")
        if channels not in (1, 2):
            raise RTPParseError(
                "rfcvoip public audio currently supports mono or stereo only."
            )
        return channels

    def _codec_adapter(
        self,
        codec: PayloadType,
        payload_type: Optional[int] = None,
        *,
        receive: bool = False,
    ):
        adapters = self._receive_codec_adapters if receive else self._codec_adapters
        adapter = adapters.get(codec)
        if adapter is not None:
            return adapter

        adapter = self._new_codec_adapter(codec, payload_type)
        adapters[codec] = adapter
        return adapter

    def _new_codec_adapter(
        self,
        codec: PayloadType,
        payload_type: Optional[int] = None,
    ):
        from rfcvoip.codecs import create_codec

        try:
            adapter = create_codec(
                codec,
                source_sample_rate=self.audio_sample_rate,
                source_sample_width=self.audio_sample_width,
                source_bit_depth=self.audio_bit_depth,
                source_channels=self.audio_channels,
            )
        except NotImplementedError as ex:
            raise UnsupportedCodecError(
                codec,
                payload_type=payload_type,
                operation="initialise",
                reason=str(ex) or "codec adapter is not implemented",
            ) from ex
        except RuntimeError as ex:
            raise UnsupportedCodecError(
                codec,
                payload_type=payload_type,
                operation="initialise",
                reason=str(ex) or _codec_unavailable_reason(codec),
            ) from ex

        if adapter is None:
            raise UnsupportedCodecError(
                codec,
                payload_type=payload_type,
                operation="initialise",
                reason=_codec_unavailable_reason(codec),
            )

        configure_fmtp = getattr(adapter, "configure_fmtp", None)
        if callable(configure_fmtp):
            if payload_type is None:
                payload_type = (
                    self.preference_payload_type
                    if codec == self.preference
                    else next(
                        (
                            pt for pt, mapped in self.assoc.items()
                            if mapped == codec
                        ),
                        None,
                    )
                )
            try:
                configure_fmtp(list(self.codec_fmtp.get(payload_type, [])))
            except ValueError as ex:
                raise UnsupportedCodecError(
                    codec,
                    payload_type=payload_type,
                    operation="initialise",
                    reason=str(ex),
                ) from ex

        return adapter

    def audio_frame_size(self, duration_ms: Optional[int] = None) -> int:
        return self._codec_adapter(self.preference).source_frame_size(
            duration_ms
        )

    def public_audio_format(
        self,
        duration_ms: int = 20,
    ) -> PublicAudioFormat:
        return PublicAudioFormat(
            sample_rate=self.audio_sample_rate,
            channels=self.audio_channels,
            bit_depth=self.audio_bit_depth,
            frame_ms=duration_ms,
        )

    def audio_format(self, duration_ms: int = 20) -> Dict[str, Any]:
        return self.public_audio_format(duration_ms).as_dict()

    def _find_telephone_event_payload_type(self) -> Optional[int]:
        for payload_type, codec in self.assoc.items():
            if codec == PayloadType.EVENT:
                try:
                    number = int(payload_type)
                except (TypeError, ValueError):
                    continue
                if 0 <= number <= 127:
                    return number
        return None

    def _build_rtp_packet(
        self,
        payload_type: int,
        payload: bytes,
        *,
        marker: bool,
        timestamp: int,
    ) -> bytes:
        try:
            payload_type = int(payload_type)
        except (TypeError, ValueError) as ex:
            raise RTPParseError(
                f"RTP payload type must be an integer, got {payload_type!r}."
            ) from ex

        if payload_type < 0 or payload_type > 127:
            raise RTPParseError(
                f"RTP payload type must be between 0 and 127, got {payload_type}."
            )

        packet = b"\x80"
        packet += (((0x80 if marker else 0x00) | (payload_type & 0x7F))).to_bytes(1, 'big')
        packet += (self.outSequence & 0xFFFF).to_bytes(2, 'big')
        packet += (timestamp & 0xFFFFFFFF).to_bytes(4, 'big')
        packet += (self.outSSRC & 0xFFFFFFFF).to_bytes(4, 'big')
        packet += payload
        return packet

    def _send_rtp_packet(
        self,
        payload_type: int,
        payload: bytes,
        *,
        marker: bool,
        timestamp: int,
    ) -> None:
        with self._socket_lock:
            # Immediate DTMF sends may race the audio transmitter. Reserve
            # and send each sequence number under the same lock used by stop.
            packet = self._build_rtp_packet(
                payload_type,
                payload,
                marker=marker,
                timestamp=timestamp,
            )
            sout = getattr(self, "sout", None)
            if (
                sout is None
                or not self.NSD
                or self.sendrecv in (
                    TransmitType.RECVONLY, TransmitType.INACTIVE,
                )
            ):
                return
            try:
                sout.sendto(
                    packet,
                    self._socket_address(self.outIP, self.outPort),
                )
            except OSError:
                if self.NSD:
                    warnings.warn(
                        "RTP Packet failed to send!",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            self.outSequence = (self.outSequence + 1) & 0xFFFF

    def _build_telephone_event_payload(
        self,
        event_code: int,
        duration: int,
        *,
        end: bool,
        volume: int,
    ) -> bytes:
        return bytes([
            event_code & 0xFF,
            ((0x80 if end else 0x00) | (volume & 0x3F)),
            (duration >> 8) & 0xFF,
            duration & 0xFF,
        ])

    @staticmethod
    def _ip_version(address: str) -> Optional[int]:
        try:
            text = str(address or "").strip().strip("[]")
            if text in ("", "0.0.0.0", "::"):
                return None
            return ipaddress.ip_address(text).version
        except ValueError:
            return None

    @classmethod
    def _select_socket_family(cls, inIP: str, outIP: str):
        in_version = cls._ip_version(inIP)
        out_version = cls._ip_version(outIP)

        if (
            in_version is not None
            and out_version is not None
            and in_version != out_version
        ):
            raise RTPParseError(
                f"RTP local address {inIP!r} and remote address {outIP!r} "
                + "use different IP versions."
            )

        version = in_version or out_version or 4
        return socket.AF_INET6 if version == 6 else socket.AF_INET

    def _socket_address(self, host: str, port: int):
        host = str(host or "").strip().strip("[]")
        if self._socket_family == socket.AF_INET6:
            if host in ("", "0.0.0.0", "::"):
                host = "::"
            return (host, port, 0, 0)
        if host in ("", "0.0.0.0", "::"):
            host = "0.0.0.0"
        return (host, port)

    def sendDTMF(self, code: str) -> bool:
        warnings.warn(
            "sendDTMF is deprecated due to PEP8 compliance. Use send_dtmf instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.send_dtmf(code)

    def send_dtmf(self, code: str) -> bool:
        code = normalize_dtmf_digit(code)
        if not code:
            return False
        return self.send_dtmf_sequence(code)

    def send_dtmf_sequence(
        self,
        digits: str,
        *,
        allow_abcd: bool = True,
    ) -> bool:
        digits = normalize_dtmf_sequence(digits, allow_abcd=allow_abcd)
        if not digits:
            return False
        if self._telephone_event_pt is None:
            return False
        with self._dtmf_lock:
            self._pending_dtmf.extend(digits)
        return True

    @property
    def NSD(self) -> bool:
        return self._running.is_set()

    @NSD.setter
    def NSD(self, running: bool) -> None:
        if running:
            self._running.set()
        else:
            self._running.clear()

    def transmit_dtmf(
        self,
        code: str,
        *,
        duration_ms: int = 200,
        packet_ms: int = 50,
        volume: int = 10,
    ) -> bool:
        if self._telephone_event_pt is None:
            return False
        if (
            not self.NSD
            or self.sendrecv in (TransmitType.RECVONLY, TransmitType.INACTIVE)
        ):
            return False

        code = str(code or '').strip().upper()
        if code not in _DTMF_CHAR_TO_EVENT:
            return False

        clock = 8000
        event_code = _DTMF_CHAR_TO_EVENT[code]
        start_timestamp = self.outTimestamp & 0xFFFFFFFF
        total_duration = max(1, int((duration_ms * clock) / 1000))
        step = max(1, int((packet_ms * clock) / 1000))
        # Report duration and update spacing must advance at the same rate.
        # A 10 ms sleep floor otherwise stretches a requested 1 ms update
        # interval into a tone ten times longer than its encoded duration.
        interval_s = step / clock

        completed = 0
        while completed < total_duration and self.NSD:
            # RFC 4733 section 2.5.1.3 requires continuation segments,
            # rather than wrapping the 16-bit duration of a long keypress.
            segment_duration = min(0xFFFF, total_duration - completed)
            segment_timestamp = (start_timestamp + completed) & 0xFFFFFFFF
            first_duration = min(step, segment_duration)
            first_payload = self._build_telephone_event_payload(
                event_code, first_duration, end=False, volume=volume
            )
            self._send_rtp_packet(
                self._telephone_event_pt,
                first_payload,
                marker=completed == 0,
                timestamp=segment_timestamp,
            )

            elapsed = first_duration
            while elapsed + step < segment_duration and self.NSD:
                time.sleep(interval_s)
                elapsed += step
                payload = self._build_telephone_event_payload(
                    event_code, elapsed, end=False, volume=volume
                )
                self._send_rtp_packet(
                    self._telephone_event_pt,
                    payload,
                    marker=False,
                    timestamp=segment_timestamp,
                )

            final_payload = self._build_telephone_event_payload(
                event_code,
                segment_duration,
                end=completed + segment_duration == total_duration,
                volume=volume,
            )
            for repeat in range(3):
                if not self.NSD:
                    break
                time.sleep(interval_s)
                self._send_rtp_packet(
                    self._telephone_event_pt,
                    final_payload,
                    marker=False,
                    timestamp=segment_timestamp,
                )
            completed += segment_duration

        # Carry the elapsed event clock into the next payload's timestamp
        # base when switching rates on this SSRC (RFC 7160 section 4.2).
        self.outTimestamp = (start_timestamp + total_duration) & 0xFFFFFFFF
        return True

    def start(self) -> None:
        with self._socket_lock:
            if self.NSD:
                raise RuntimeError("Attempted to start already started RTPClient")
            if any(worker.is_alive() for worker in self._workers):
                raise RuntimeError("Previous RTPClient workers are still stopping")

            sin = socket.socket(self._socket_family, socket.SOCK_DGRAM)
            try:
                sin.bind(self._socket_address(self.inIP, self.inPort))
                sin.setblocking(False)
            except Exception:
                sin.close()
                raise

            self.sin = sin
            # Some systems just reply to the port they receive from instead of
            # listening to the SDP.
            self.sout = self.sin
            # A new socket lifetime begins a fresh inbound stream. Retaining
            # the previous timestamp origin, decoder and duplicate history
            # would discard a peer's restarted sequence/timestamp range.
            self._receive_ssrc = self._receive_timestamp = None
            self._receive_codec_adapters.clear()
            self._receive_sequences.clear()
            self._receive_sequence_order.clear()
            self._receive_media_sequence = self._receive_media_timestamp = None
            self._receive_clock_epochs.clear()
            self._receive_media_history.clear()
            self._receive_event_end = None
            self._received_dtmf.clear()
            self._received_dtmf_order.clear()
            self._received_dtmf_segments.clear()
            self.pmin.rebuild(True)
            self.NSD = True
            try:
                self._workers = [
                    Thread(target=self.recv, name="RTP Receiver", daemon=True),
                    Thread(target=self.trans, name="RTP Transmitter", daemon=True),
                ]
                for worker in self._workers:
                    worker.start()
            except Exception:
                self.NSD = False
                self.sin = self.sout = None
                sin.close()
                raise

    def stop(self) -> None:
        with self._socket_lock:
            self.NSD = False
            sin = getattr(self, "sin", None)
            sout = getattr(self, "sout", None)
            self.sin = None
            self.sout = None
            workers = list(self._workers)

        if sin is not None:
            sin.close()
        if sout is not None and sout is not sin:
            sout.close()
        for worker in workers:
            # A DTMF callback can stop the client from its receiver thread.
            # A blocked codec must not make shutdown wait indefinitely; start
            # refuses to revive the shared running flag until it exits.
            if worker is not threading.current_thread() and worker.is_alive():
                worker.join(timeout=1.0)

    def read(
        self,
        length: Optional[int] = None,
        blocking: bool = True,
    ) -> bytes:
        if length is None:
            length = self.audio_frame_size()
        if not blocking or length <= 0:
            return self.pmin.read(length)
        chunks = []
        remaining = length
        while remaining and self.NSD:
            # Drain incrementally so a read larger than the bounded receive
            # buffer can complete without discarding its early audio. Keep
            # availability and consumption atomic across concurrent readers.
            with self.pmin.bufferLock:
                count = min(remaining, self.pmin.available())
                if count:
                    chunks.append(self.pmin.read(count))
                    remaining -= count
            if not count:
                time.sleep(0.01)
        if remaining:
            chunks.append(self.pmin.read(remaining))
        return b"".join(chunks)

    def write(self, data: bytes) -> None:
        with self.pmout.bufferLock:
            self.pmout.write(self.outOffset, data)
            self.outOffset += len(data)

    def recv(self) -> None:
        while self.NSD:
            try:
                sin = getattr(self, "sin", None)
                if sin is None:
                    time.sleep(0.01)
                    continue
                # Receive the entire UDP datagram. An undersized buffer
                # truncates RTP on Unix and raises WSAEMSGSIZE on Windows.
                packet = sin.recv(65535)
                if self.sendrecv in (
                    TransmitType.SENDRECV,
                    TransmitType.RECVONLY,
                ):
                    self.parse_packet(packet)
            except BlockingIOError:
                time.sleep(0.01)
            except RTPParseError as e:
                debug(str(e))
            except OSError as ex:
                if self.NSD:
                    if ex.errno in (errno.EBADF, errno.ENOTSOCK) or getattr(
                        ex, "winerror", None
                    ) == 10038:
                        # This descriptor cannot recover. Release media
                        # workers/readers instead of retrying it forever.
                        self.stop()
                        return
                    time.sleep(0.01)

    def trans(
        self,
        *,
        clock: Optional[Callable[[], float]] = None,
        wait: Optional[Callable[[float], None]] = None,
    ) -> None:
        """Pace audio against absolute media deadlines without sleep drift."""
        clock = clock or time.perf_counter
        wait = wait or time.sleep
        next_due = clock()
        previous_send = None
        while self.NSD:
            if self.sendrecv in (TransmitType.RECVONLY, TransmitType.INACTIVE):
                wait(0.02)
                next_due = clock()
                continue

            with self._dtmf_lock:
                pending_dtmf = self._pending_dtmf.popleft() if self._pending_dtmf else None
            if pending_dtmf is not None:
                self.transmit_dtmf(pending_dtmf)
                next_due = clock()
                continue

            adapter = self._codec_adapter(
                self.preference,
                self.preference_payload_type,
            )
            size = adapter.source_frame_size()
            period = adapter.packet_duration_seconds(bytes(size))
            # Ordinary wakeup jitter is absorbed by the next absolute deadline.
            # A whole missed interval starts a fresh epoch, preventing bursts
            # of old packets after an event-loop or encoder stall.
            missed = max(0, int((clock() - next_due) / period + 1e-9))
            if missed:
                next_due = clock()
            self.pmout.configure(size, period, self.pmout.silence_byte)
            raw_payload = self.pmout.read(size)
            sent_at = None
            try:
                payload = adapter.encode(raw_payload)
            except Exception as ex:
                warnings.warn(
                    f"RTP audio encode failed for {self.preference}: {ex}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                increment = adapter.rtp_timestamp_increment(raw_payload, b"")
                # PCM has been consumed even though this interval could not
                # be encoded. Do not reuse its timestamp on the next packet.
                self.outTimestamp = (
                    self.outTimestamp + (missed + 1) * increment
                ) & 0xFFFFFFFF
            else:
                increment = adapter.rtp_timestamp_increment(raw_payload, payload)
                timestamp = (self.outTimestamp + missed * increment) & 0xFFFFFFFF
                sequence = self.outSequence & 0xFFFF
                sent_at = clock()
                self._send_rtp_packet(
                    self.preference_payload_type,
                    payload,
                    marker=False,
                    timestamp=timestamp,
                )
                self.outTimestamp = (timestamp + increment) & 0xFFFFFFFF

            next_due += period
            now = clock()
            if now > next_due:
                skipped = int((now - next_due) / period + 1e-9) + 1
                missed += skipped
                self.outTimestamp = (
                    self.outTimestamp + skipped * increment
                ) & 0xFFFFFFFF
                next_due = now + period
            trace = getattr(self, "_callbot_audio_trace", None)
            if trace is not None and sent_at is not None:
                try:
                    trace(
                        "sip_rtp_output",
                        sequence=sequence,
                        rtp_timestamp=timestamp,
                        ssrc=self.outSSRC,
                        pcm_bytes=len(raw_payload),
                        packet_duration_ms=period * 1000,
                        rtp_clock_rate=adapter.rate,
                        codec=str(self.preference),
                        skipped_deadlines=missed,
                        send_interval_ms=(
                            None if previous_send is None
                            else (sent_at - previous_send) * 1000
                        ),
                        **self.pmout.snapshot(),
                    )
                except Exception:
                    # Optional diagnostics cannot interrupt live media.
                    pass
            if sent_at is not None:
                previous_send = sent_at
            # TRANSMIT_DELAY_REDUCTION is kept as a compatibility setting, but
            # must not speed up the RTP sample clock to compensate for jitter.
            wait(max(0.0, next_due - clock()))

    @property
    def trans_delay_reduction(self) -> float:
        try:
            reduction = float(rfcvoip.TRANSMIT_DELAY_REDUCTION) + 1.0
        except (TypeError, ValueError):
            return 1.0
        return reduction if reduction > 0 else 1.0

    def parsePacket(self, packet: bytes) -> None:
        warnings.warn(
            "parsePacket is deprecated due to PEP8 compliance. "
            + "Use parse_packet instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_packet(packet)

    def parse_packet(self, packet: bytes) -> None:
        msg = RTPMessage(packet, self.receive_assoc)
        if msg.payload_type == PayloadType.EVENT:
            self.parse_telephone_event(msg)
        elif is_audio_codec(msg.payload_type):
            self.parse_audio(msg)
        else:
            raise UnsupportedCodecError(
                msg.payload_type,
                payload_type=getattr(msg, "payload_number", None),
                operation="parse",
                reason=(
                    payload_type_media_kind(msg.payload_type)
                    + " RTP payloads are not supported by RTPClient"
                ),
            )

    def _receive_media_offset(
        self,
        timestamp: int,
        clock: int,
        sequence: Optional[int],
        offset_for_timestamp: Optional[Callable[[int], int]] = None,
        event_duration: Optional[int] = None,
    ) -> int:
        """Map RTP clock changes and reordered packets onto one PCM timeline."""
        previous_timestamp = self._receive_media_timestamp
        if previous_timestamp is not None:
            timestamp = previous_timestamp + (
                (timestamp - previous_timestamp + 0x80000000) % 0x100000000
                - 0x80000000
            )
        previous_sequence = self._receive_media_sequence
        if sequence is None:
            sequence = (
                0 if previous_sequence is None else previous_sequence + 1
            )
        elif previous_sequence is not None:
            sequence = previous_sequence + (
                (sequence - previous_sequence + 0x8000) % 0x10000 - 0x8000
            )

        alignment = self.audio_sample_width * self.audio_channels

        def offset_at(value: int, rate: int) -> int:
            return (value * self.audio_sample_rate // rate) * alignment

        is_audio = offset_for_timestamp is not None
        if not is_audio:
            offset_for_timestamp = lambda value: offset_at(value, clock)
        epochs = self._receive_clock_epochs
        if not epochs:
            epochs.append(
                (sequence, timestamp, clock, offset_for_timestamp(timestamp))
            )
        forward = (
            previous_sequence is None or sequence >= previous_sequence
        ) and (previous_timestamp is None or timestamp >= previous_timestamp)
        if forward and clock != epochs[-1][2]:
            _, origin, previous_clock, pcm_origin = epochs[-1]
            transition = timestamp
            if (
                is_audio
                and previous_clock == PayloadType.EVENT.rate
                and self._receive_event_end is not None
                and origin <= self._receive_event_end <= timestamp
            ):
                transition = self._receive_event_end
            pcm_offset = pcm_origin + (
                offset_at(transition, previous_clock)
                - offset_at(origin, previous_clock)
            )
            epochs.append((sequence, transition, clock, pcm_offset))
        epoch = next(
            (item for item in reversed(epochs) if item[0] <= sequence),
            epochs[0],
        )
        if not forward and is_audio and epoch[2] != clock:
            # The first resumed audio packet may itself arrive after a later
            # audio packet established this clock. Place it relative to that
            # known audio epoch rather than the preceding event's rate.
            epoch = next(
                (
                    item
                    for item in epochs
                    if item[0] > sequence and item[2] == clock
                ),
                epoch,
            )
        _, origin, _rate, pcm_origin = epoch
        result = pcm_origin + (
            offset_for_timestamp(timestamp) - offset_for_timestamp(origin)
        )
        if forward:
            self._receive_media_sequence = sequence
            self._receive_media_timestamp = timestamp
            if is_audio:
                self._receive_event_end = None
            elif event_duration is not None:
                self._receive_event_end = timestamp + event_duration
            elif (
                self._receive_event_end is not None
                and timestamp >= self._receive_event_end
            ):
                self._receive_event_end = None
        event_end = (
            None if event_duration is None else timestamp + event_duration
        )
        observation = (sequence, timestamp, clock, result, is_audio, event_end)
        history = self._receive_media_history
        oldest_sequence = min((item[0] for item in history), default=sequence)
        repair_sequence = sequence
        if not is_audio:
            repair_sequence = min(
                [sequence]
                + [
                    item[0]
                    for item in history
                    if not item[4] and item[1] == timestamp
                ]
                + [
                    item[0] - 1
                    for item in history
                    if item[4] and item[1] > timestamp
                ]
            )
        can_repair = bool(history) and repair_sequence >= oldest_sequence
        if sequence >= oldest_sequence and observation not in history:
            history.append(observation)
        if not forward and not is_audio and can_repair:
            self._repair_late_event_clock(repair_sequence, timestamp)
        return result

    def _repair_late_event_clock(self, sequence: int, timestamp: int) -> None:
        history = self._receive_media_history
        prefix = [
            item for item in self._receive_clock_epochs if item[0] < sequence
        ]
        if not prefix:
            return
        observations = sorted(
            (item for item in history if item[0] >= sequence),
            key=lambda item: (item[0], item[1]),
        )
        known_ends = {}
        for _, stamp, _, _, audio, end in observations:
            if not audio and end is not None:
                known_ends[stamp] = max(end, known_ends.get(stamp, end))
        if not any(
            item[0] == sequence and item[1] == timestamp and not item[4]
            for item in observations
        ):
            # Initial reports can be lost while a final retransmission is
            # sent after audio resumes. Its timestamp still proves that the
            # event began before the first later audio capture, regardless
            # of the retransmission's own sequence number.
            observations.insert(
                0,
                (
                    sequence,
                    timestamp,
                    PayloadType.EVENT.rate,
                    0,
                    False,
                    known_ends.get(timestamp),
                ),
            )
        alignment = self.audio_sample_width * self.audio_channels

        def offset_at(value: int, rate: int) -> int:
            return value * self.audio_sample_rate // rate * alignment

        epochs = list(prefix)
        event_end = None
        updates = []
        first_audio = None
        correction = None
        latest_stamp = epochs[-1][1]
        for seq, stamp, rate, old_offset, audio, end in observations:
            if not audio:
                end = known_ends.get(stamp, end)
                if stamp < latest_stamp:
                    # A final report can be retransmitted after audio has
                    # resumed. Its end time applies to the earlier event;
                    # it must not switch the current clock back again.
                    updates.append((seq, stamp, rate, old_offset, audio, end))
                    continue
            latest_stamp = max(latest_stamp, stamp)
            _, origin, previous_rate, pcm_origin = epochs[-1]
            if rate != previous_rate:
                transition = stamp
                if (
                    audio
                    and previous_rate == PayloadType.EVENT.rate
                    and (
                        event_end is not None and origin <= event_end <= stamp
                    )
                ):
                    transition = event_end
                pcm_origin += offset_at(transition, previous_rate) - offset_at(
                    origin, previous_rate
                )
                origin = transition
                epochs.append((seq, origin, rate, pcm_origin))
            new_offset = (
                pcm_origin + offset_at(stamp, rate) - offset_at(origin, rate)
            )
            if audio:
                delta = new_offset - old_offset
                if first_audio is None:
                    first_audio, correction = old_offset, delta
                elif delta != correction:
                    # Incomplete history cannot safely identify one suffix.
                    return
                event_end = None
            elif end is not None:
                event_end = end
            elif event_end is not None and stamp >= event_end:
                event_end = None
            updates.append((seq, stamp, rate, new_offset, audio, end))
        if first_audio is None or not correction:
            return
        if not self._shift_unread_receive_suffix(first_audio, correction):
            return
        self._receive_clock_epochs = deque(epochs, maxlen=256)
        changed = {
            (item[0], item[1], item[2], item[4]): item for item in updates
        }
        self._receive_media_history = deque(
            (
                changed.get((item[0], item[1], item[2], item[4]), item)
                for item in history
            ),
            maxlen=256,
        )
        self._receive_event_end = event_end

    def _shift_unread_receive_suffix(self, boundary: int, delta: int) -> bool:
        manager = self.pmin
        with manager.bufferLock:
            cursor = manager.offset + manager.buffer.tell()
            if delta < 0 and any(
                start < boundary and start + len(data) > boundary + delta
                for start, data in manager.log.items()
            ):
                return False
            shifted = {}
            for start, data in manager.log.items():
                split = max(0, min(len(data), boundary - start))
                if split:
                    shifted[start] = data[:split]
                old_start = max(start + split, cursor)
                suffix = data[old_start - start :]
                new_start = old_start + delta
                if new_start < cursor:
                    suffix = suffix[cursor - new_start :]
                    new_start = cursor
                if suffix:
                    shifted[new_start] = suffix
            if (
                shifted
                and max(shifted) - manager.offset > manager.max_log_span
            ):
                cutoff = max(shifted) - manager.max_log_span
                shifted = {
                    max(start, cutoff): data[max(0, cutoff - start) :]
                    for start, data in shifted.items()
                    if start + len(data) > cutoff
                }
                manager.offset = min(shifted)
                manager.buffer = io.BytesIO()
                manager._read_started = False
            manager.log = shifted
            manager.rebuild(False)
        return True

    def parse_audio(
        self,
        packet: RTPMessage,
        codec: Optional[PayloadType] = None,
    ) -> None:
        codec = codec or packet.payload_type
        ssrc = getattr(packet, "SSRC", 0)
        same_source = self._receive_ssrc == ssrc
        sequence = getattr(packet, "sequence", None)
        sequence_key = (ssrc, sequence)
        if sequence is not None and sequence_key in self._receive_sequences:
            return

        if not same_source and self._receive_ssrc is not None:
            adapter = self._new_codec_adapter(
                codec, getattr(packet, "payload_number", None),
            )
        else:
            adapter = self._codec_adapter(
                codec,
                getattr(packet, "payload_number", None),
                receive=True,
            )
        timestamp = packet.timestamp
        previous = self._receive_timestamp if same_source else None
        if previous is not None:
            # Choose the closest timestamp in the extended 32-bit timeline.
            # Keep the high-water mark so a reordered pre-wrap packet cannot
            # move subsequent packets into the wrong timestamp cycle.
            timestamp = previous + (
                (timestamp - (previous & 0xFFFFFFFF) + 0x80000000)
                % 0x100000000 - 0x80000000
            )
            if timestamp < previous:
                # Reordered audio can still fill an unread buffer gap. Its
                # decoder must not rewind the resampler/predictor history
                # used by subsequent packets on the forward timeline.
                adapter = self._new_codec_adapter(
                    codec,
                    getattr(packet, "payload_number", None),
                )
        try:
            data = adapter.decode(packet.payload)
        except NotImplementedError as ex:
            raise UnsupportedCodecError(
                codec,
                payload_type=getattr(packet, "payload_number", None),
                operation="decode",
                reason=(
                    str(ex)
                    or "decode() is not implemented by the codec adapter"
                ),
            ) from ex
        except Exception as ex:
            raise RTPParseError(
                f"RTP audio decode failed for {codec}: {ex}"
            ) from ex
        if not same_source:
            # A new source has its own timestamp origin and decoder history.
            # Commit the switch only after its first audio decoded correctly.
            self._receive_codec_adapters = {codec: adapter}
            self._receive_ssrc = ssrc
            self._receive_media_sequence = self._receive_media_timestamp = None
            self._receive_clock_epochs.clear()
            self._receive_media_history.clear()
            self._receive_event_end = None
        offset = self._receive_media_offset(
            timestamp,
            int(getattr(adapter, "rate", codec.rate)),
            sequence,
            adapter.output_offset,
        )
        if not same_source:
            self.pmin.rebuild(True, offset, data)
        else:
            self.pmin.write(offset, data)
        if previous is None or timestamp > previous:
            self._receive_timestamp = timestamp
        if sequence is not None:
            # Keep recent duplicate history across source switches. A delayed
            # duplicate of the previous SSRC must not switch playback back and
            # erase the replacement source's queued PCM.
            self._receive_sequences.add(sequence_key)
            self._receive_sequence_order.append(sequence_key)
            if len(self._receive_sequence_order) > 256:
                self._receive_sequences.remove(self._receive_sequence_order.popleft())

    def encodePacket(self, payload: bytes) -> bytes:
        warnings.warn(
            "encodePacket is deprecated due to PEP8 compliance. "
            + "Use encode_packet instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_packet(payload)

    def encode_packet(self, payload: bytes) -> bytes:
        try:
            return self._codec_adapter(
                self.preference,
                self.preference_payload_type,
            ).encode(payload)
        except NotImplementedError as ex:
            raise UnsupportedCodecError(
                self.preference,
                payload_type=self.preference_payload_type,
                operation="encode",
                reason=(
                    str(ex)
                    or "encode() is not implemented by the codec adapter"
                ),
            ) from ex

    def parsePCMU(self, packet: RTPMessage) -> None:
        warnings.warn(
            "parsePCMU is deprecated due to PEP8 compliance. "
            + "Use parse_pcmu instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_pcmu(packet)

    def parse_pcmu(self, packet: RTPMessage) -> None:
        self.parse_audio(packet, PayloadType.PCMU)

    def encodePCMU(self, packet: bytes) -> bytes:
        warnings.warn(
            "encodePCMU is deprecated due to PEP8 compliance. "
            + "Use encode_pcmu instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_pcmu(packet)

    def encode_pcmu(self, packet: bytes) -> bytes:
        return self._codec_adapter(PayloadType.PCMU).encode(packet)

    def parsePCMA(self, packet: RTPMessage) -> None:
        warnings.warn(
            "parsePCMA is deprecated due to PEP8 compliance. "
            + "Use parse_pcma instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_pcma(packet)

    def parse_pcma(self, packet: RTPMessage) -> None:
        self.parse_audio(packet, PayloadType.PCMA)

    def encodePCMA(self, packet: bytes) -> bytes:
        warnings.warn(
            "encodePCMA is deprecated due to PEP8 compliance. "
            + "Use encode_pcma instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_pcma(packet)

    def encode_pcma(self, packet: bytes) -> bytes:
        return self._codec_adapter(PayloadType.PCMA).encode(packet)

    def parseTelephoneEvent(self, packet: RTPMessage) -> None:
        warnings.warn(
            "parseTelephoneEvent "
            + "is deprecated due to PEP8 compliance. "
            + "Use parse_telephone_event instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_telephone_event(packet)

    def parse_telephone_event(self, packet: RTPMessage) -> None:
        payload = packet.payload
        if len(payload) < 4:
            raise RTPParseError("telephone-event payload too short.")
        if len(payload) % 4:
            raise RTPParseError(
                "telephone-event payload contains a partial report."
            )
        # RFC 4733 permits consecutive event reports in one RTP payload.
        # Validate all reports before invoking any application callback.
        for offset in range(0, len(payload), 4):
            event_code = payload[offset]
            if event_code >= len(_DTMF_EVENT_TO_CHAR):
                raise RTPParseError(
                    f"Unsupported telephone-event code {event_code}."
                )

        timestamp = packet.timestamp
        marker = packet.marker
        packed = len(payload) > 4
        for offset in range(0, len(payload), 4):
            report = payload[offset : offset + 4]
            if self._receive_ssrc == packet.SSRC:
                self._receive_media_offset(
                    timestamp,
                    PayloadType.EVENT.rate,
                    packet.sequence,
                    event_duration=(
                        add_bytes(report[2:4]) if report[1] & 0x80 else None
                    ),
                )
            self._parse_telephone_event_report(
                report,
                packet.SSRC,
                timestamp,
                marker=marker,
                packed=packed,
            )
            timestamp = (timestamp + add_bytes(report[2:4])) & 0xFFFFFFFF
            marker = bool(report[1] & 0x80)

    def _parse_telephone_event_report(
        self,
        payload: bytes,
        ssrc: int,
        timestamp: int,
        *,
        marker: bool,
        packed: bool,
    ) -> None:
        event_code = payload[0]
        event = _DTMF_EVENT_TO_CHAR[event_code]
        if self.dtmf is None:
            return

        duration = add_bytes(payload[2:4])
        end = bool(payload[1] & 0x80)
        event_key = (ssrc, timestamp, event_code)
        if event_key in self._received_dtmf:
            previous_duration, previous_end = self._received_dtmf_segments[
                event_key
            ]
            self._received_dtmf_segments[event_key] = (
                max(duration, previous_duration),
                end or previous_end,
            )
            return

        # Any update identifies the event, even if its marked first packet
        # was lost. RFC 4733 splits events longer than 65535 clock ticks into
        # unmarked continuation segments; those must not become extra digits.
        preceding_segment = self._received_dtmf_segments.get(
            (
                ssrc,
                (timestamp - 65535) & 0xFFFFFFFF,
                event_code,
            )
        )
        # A packed packet's marker can belong to a later event, so it must
        # not restart a known long-event continuation in an earlier report.
        continuation = (
            (not marker or packed) and preceding_segment == (65535, False)
        )
        self._received_dtmf.add(event_key)
        self._received_dtmf_order.append(event_key)
        self._received_dtmf_segments[event_key] = (duration, end)
        if len(self._received_dtmf_order) > 256:
            expired = self._received_dtmf_order.popleft()
            self._received_dtmf.remove(expired)
            self._received_dtmf_segments.pop(expired, None)
        if not continuation:
            try:
                self.dtmf(event)
            except Exception as ex:
                warnings.warn(
                    f"DTMF callback failed: {ex}",
                    RuntimeWarning,
                    stacklevel=2,
                )
