from enum import Enum
from threading import Timer
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union
from collections import deque
import audioop
import io
import ipaddress
import pyVoIP
import random
import socket
import threading
import time
import warnings


__all__ = [
    "add_bytes",
    "byte_to_bits",
    "DynamicPayloadType",
    "codec_info",
    "is_audio_codec",
    "is_transmittable_audio_codec",
    "is_video_codec",
    "payload_type_from_name",
    "payload_type_media_kind",
    "supported_codecs",
    "PayloadType",
    "select_transmittable_audio_codec",
    "RTPParseError",
    "RTPProtocol",
    "RTPPacketManager",
    "RTPClient",
    "TransmitType",
]

debug = pyVoIP.debug

_DTMF_EVENT_TO_CHAR = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#", "A", "B", "C", "D",
]
_DTMF_CHAR_TO_EVENT = {char: idx for idx, char in enumerate(_DTMF_EVENT_TO_CHAR)}


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
        # RFC 3551 defines MP2T as both audio and video.  It is classified as
        # audio here for reporting, but it is intentionally not transmittable
        # because PyVoIP does not implement an MP2T encoder.
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

_ENCODABLE_AUDIO_PAYLOAD_TYPES = frozenset(
    (
        PayloadType.PCMU,
        PayloadType.PCMA,
    )
)


def payload_type_media_kind(codec: PayloadType) -> str:
    """Return PyVoIP's broad media classification for an RTP payload type.

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
    if codec in _AUDIO_PAYLOAD_TYPES:
        return "audio"
    if codec in _VIDEO_PAYLOAD_TYPES:
        return "video"
    return "unknown"


def is_audio_codec(codec: PayloadType) -> bool:
    """Return whether ``codec`` represents an RTP audio payload type."""
    return codec in _AUDIO_PAYLOAD_TYPES


def is_video_codec(codec: PayloadType) -> bool:
    """Return whether ``codec`` represents an RTP video payload type."""
    return codec in _VIDEO_PAYLOAD_TYPES


def is_transmittable_audio_codec(codec: PayloadType) -> bool:
    """Return whether PyVoIP can encode ``codec`` as the main audio stream."""
    if codec not in _ENCODABLE_AUDIO_PAYLOAD_TYPES:
        return False
    if codec not in getattr(pyVoIP, "RTPCompatibleCodecs", ()):  # pragma: no branch
        return False
    try:
        int(codec)
    except (DynamicPayloadType, TypeError, ValueError):
        return False
    return True


def select_transmittable_audio_codec(
    assoc: Dict[int, PayloadType]
) -> Tuple[int, PayloadType]:
    """Select the negotiated payload number and codec used for RTP audio.

    ``assoc`` maps negotiated RTP payload numbers to :class:`PayloadType`
    values.  Returning the negotiated payload number is important when a peer
    maps a known codec, such as PCMU, to a dynamic payload number via SDP
    ``rtpmap``.
    """
    rejected = []
    for payload_type, codec in assoc.items():
        try:
            payload_number = int(payload_type)
        except (TypeError, ValueError):
            rejected.append(f"{payload_type}:{codec} (invalid payload number)")
            continue

        if is_transmittable_audio_codec(codec):
            return payload_number, codec

        rejected.append(
            f"{payload_number}:{codec} ({payload_type_media_kind(codec)})"
        )

    detail = ": " + ", ".join(rejected) if rejected else "."
    raise RTPParseError("No transmittable audio codec negotiated" + detail)


def payload_type_from_name(name: str) -> PayloadType:
    """Return a :class:`PayloadType` matching an SDP codec name.

    SDP ``rtpmap`` lines identify dynamic payloads by encoding names such as
    ``telephone-event`` or ``opus``.  Static payloads are often represented by
    their payload number, but some peers still include an ``rtpmap`` name like
    ``PCMU``.  This helper resolves the names known by PyVoIP without relying
    on hard-coded codec lists outside this module.
    """
    needle = str(name or "").strip().lower()
    if not needle:
        raise ValueError("Codec name cannot be empty.")

    for codec in PayloadType:
        names = {str(codec).lower(), codec.description.lower()}
        if isinstance(codec.value, str):
            names.add(codec.value.lower())
        if needle in names:
            return codec

    raise ValueError(f"RTP Payload type {name!r} not found.")


def codec_info(
    codec: PayloadType,
    payload_type: Optional[int] = None,
    *,
    media_type: Optional[str] = None,
    fmtp: Optional[List[str]] = None,
    source: str = "pyvoip",
    supported: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return a serializable description of an RTP codec."""
    if payload_type is None:
        try:
            payload_type = int(codec)
        except DynamicPayloadType:
            payload_type = None

    if supported is None:
        supported = codec in getattr(pyVoIP, "RTPCompatibleCodecs", [])

    return {
        "media_type": media_type,
        "payload_type": payload_type,
        "name": str(codec),
        "description": codec.description,
        "payload_kind": payload_type_media_kind(codec),
        "can_transmit_audio": is_transmittable_audio_codec(codec),
        "rate": codec.rate,
        "channels": codec.channel,
        "is_dynamic": not isinstance(codec.value, int),
        "fmtp": list(fmtp or []),
        "codec_supported": bool(supported),
        "protocol_supported": None,
        "supported": bool(supported),
        "source": source,
    }

def supported_codecs() -> List[Dict[str, Any]]:
    """Return codecs supported by this PyVoIP build/configuration."""
    return [
        codec_info(codec)
        for codec in getattr(pyVoIP, "RTPCompatibleCodecs", [])
    ]


class RTPPacketManager:
    def __init__(self):
        self.offset = 4294967296
        """
        The largest number storable in 4 bytes + 1. This will ensure the
        offset adjustment in self.write(offset, data) works.
        """
        self.buffer = io.BytesIO()
        self.bufferLock = threading.Lock()
        self.log = {}
        self.rebuilding = False

    def read(self, length: int = 160) -> bytes:
        # This acts functionally as a lock while the buffer is being rebuilt.
        while self.rebuilding:
            time.sleep(0.01)
        with self.bufferLock:
            packet = self.buffer.read(length)
            if len(packet) < length:
                packet = packet + (b"\x80" * (length - len(packet)))
        return packet

    def rebuild(self, reset: bool, offset: int = 0, data: bytes = b"") -> None:
        self.rebuilding = True
        if reset:
            self.log = {}
            self.log[offset] = data
            self.buffer = io.BytesIO(data)
        else:
            bufferloc = self.buffer.tell()
            self.buffer = io.BytesIO()
            for pkt in self.log:
                self.write(pkt, self.log[pkt])
            self.buffer.seek(bufferloc, 0)
        self.rebuilding = False

    def write(self, offset: int, data: bytes) -> None:
        rebuild_args = None
        with self.bufferLock:
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
                rebuild_args = (reset, offset, data)
            else:
                adjusted_offset = offset - self.offset
                self.buffer.seek(adjusted_offset, 0)
                self.buffer.write(data)
                self.buffer.seek(bufferloc, 0)

        if rebuild_args is not None:
            self.rebuild(*rebuild_args)

class RTPMessage:
    def __init__(self, data: bytes, assoc: Dict[int, PayloadType]):
        self.RTPCompatibleVersions = pyVoIP.RTPCompatibleVersions
        self.assoc = assoc
        # Setting defaults to stop mypy from complaining
        self.version = 0
        self.padding = False
        self.extension = False
        self.CC = 0
        self.marker = False
        self.payload_type = PayloadType.UNKNOWN
        self.sequence = 0
        self.timestamp = 0
        self.SSRC = 0

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
        if pt in self.assoc:
            self.payload_type = self.assoc[pt]
        else:
            try:
                self.payload_type = PayloadType(pt)
                e = False
            except ValueError:
                e = True
            if e:
                raise RTPParseError(f"RTP Payload type {pt} not found.")

        self.sequence = add_bytes(packet[2:4])
        self.timestamp = add_bytes(packet[4:8])
        self.SSRC = add_bytes(packet[8:12])

        self.CSRC = []

        i = 12
        for x in range(self.CC):
            self.CSRC.append(packet[i : i + 4])
            i += 4

        if self.extension:
            pass

        self.payload = packet[i:]


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
    ):
        self.NSD = True
        # Example: {0: PayloadType.PCMU, 101: PayloadType.EVENT}
        self.assoc = assoc
        debug("Selecting negotiated audio codec for transmission")
        try:
            (
                self.preference_payload_type,
                self.preference,
            ) = select_transmittable_audio_codec(assoc)
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

        self.inIP = inIP
        self.inPort = inPort
        self.outIP = outIP
        self.outPort = outPort
        self._socket_family = self._select_socket_family(inIP, outIP)

        self.dtmf = dtmf

        self.pmout = RTPPacketManager()  # To Send
        self.pmin = RTPPacketManager()  # Received
        self.outOffset = random.randint(1, 5000)

        self.outSequence = random.randint(1, 100)
        self.outTimestamp = random.randint(1, 10000)
        self.outSSRC = random.randint(1000, 65530)
        self._telephone_event_pt = self._find_telephone_event_payload_type()
        self._pending_dtmf: Deque[str] = deque()
        self._dtmf_lock = threading.Lock()

    def _find_telephone_event_payload_type(self) -> Optional[int]:
        for payload_type, codec in self.assoc.items():
            if codec == PayloadType.EVENT:
                try:
                    return int(payload_type)
                except Exception:
                    continue
        return None

    def _build_rtp_packet(
        self,
        payload_type: int,
        payload: bytes,
        *,
        marker: bool,
        timestamp: int,
    ) -> bytes:
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
        packet = self._build_rtp_packet(payload_type, payload, marker=marker, timestamp=timestamp)
        try:
            self.sout.sendto(
                packet,
                self._socket_address(self.outIP, self.outPort),
            )
        except OSError:
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
            return ipaddress.ip_address(address).version
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
        if self._socket_family == socket.AF_INET6:
            return (host, port, 0, 0)
        return (host, port)

    def sendDTMF(self, code: str) -> bool:
        warnings.warn(
            "sendDTMF is deprecated due to PEP8 compliance. Use send_dtmf instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.send_dtmf(code)

    def send_dtmf(self, code: str) -> bool:
        code = str(code or '').strip().upper()
        if code not in _DTMF_CHAR_TO_EVENT:
            return False
        if self._telephone_event_pt is None:
            return False
        with self._dtmf_lock:
            self._pending_dtmf.append(code)
        return True

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

        code = str(code or '').strip().upper()
        if code not in _DTMF_CHAR_TO_EVENT:
            return False

        clock = 8000
        event_code = _DTMF_CHAR_TO_EVENT[code]
        start_timestamp = self.outTimestamp & 0xFFFFFFFF
        total_duration = max(1, int((duration_ms * clock) / 1000))
        step = max(1, int((packet_ms * clock) / 1000))
        interval_s = max(0.01, packet_ms / 1000.0)

        first_duration = min(step, total_duration)
        first_payload = self._build_telephone_event_payload(
            event_code, first_duration, end=False, volume=volume
        )
        self._send_rtp_packet(
            self._telephone_event_pt,
            first_payload,
            marker=True,
            timestamp=start_timestamp,
        )

        elapsed = first_duration
        while elapsed + step < total_duration and self.NSD:
            time.sleep(interval_s)
            elapsed += step
            payload = self._build_telephone_event_payload(
                event_code, elapsed, end=False, volume=volume
            )
            self._send_rtp_packet(
                self._telephone_event_pt,
                payload,
                marker=False,
                timestamp=start_timestamp,
            )

        final_payload = self._build_telephone_event_payload(
            event_code, total_duration, end=True, volume=volume
        )
        for repeat in range(3):
            if not self.NSD:
                break
            time.sleep(interval_s)
            self._send_rtp_packet(
                self._telephone_event_pt,
                final_payload,
                marker=False,
                timestamp=start_timestamp,
            )

        self.outTimestamp = (start_timestamp + total_duration) & 0xFFFFFFFF
        return True

    def start(self) -> None:
        self.sin = socket.socket(self._socket_family, socket.SOCK_DGRAM)
        # Some systems just reply to the port they receive from instead of
        # listening to the SDP.
        self.sout = self.sin
        self.sin.bind(self._socket_address(self.inIP, self.inPort))
        self.sin.setblocking(False)

        r = Timer(0, self.recv)
        r.name = "RTP Receiver"
        r.daemon = True
        r.start()
        t = Timer(0, self.trans)
        t.name = "RTP Transmitter"
        t.daemon = True
        t.start()

    def stop(self) -> None:
        self.NSD = False
        self.sin.close()
        self.sout.close()

    def read(self, length: int = 160, blocking: bool = True) -> bytes:
        if not blocking:
            return self.pmin.read(length)
        packet = self.pmin.read(length)
        while packet == (b"\x80" * length) and self.NSD:
            time.sleep(0.01)
            packet = self.pmin.read(length)
        return packet

    def write(self, data: bytes) -> None:
        self.pmout.write(self.outOffset, data)
        self.outOffset += len(data)

    def recv(self) -> None:
        while self.NSD:
            try:
                packet = self.sin.recv(8192)
                self.parse_packet(packet)
            except BlockingIOError:
                time.sleep(0.01)
            except RTPParseError as e:
                debug(str(e))
            except OSError:
                pass

    def trans(self) -> None:
        while self.NSD:
            with self._dtmf_lock:
                pending_dtmf = self._pending_dtmf.popleft() if self._pending_dtmf else None
            if pending_dtmf is not None:
                self.transmit_dtmf(pending_dtmf)
                continue

            last_sent = time.monotonic_ns()
            raw_payload = self.pmout.read()
            payload = self.encode_packet(raw_payload)
            timestamp = self.outTimestamp & 0xFFFFFFFF
            self._send_rtp_packet(
                self.preference_payload_type,
                payload,
                marker=False,
                timestamp=timestamp,
            )
            self.outTimestamp = (self.outTimestamp + len(payload)) & 0xFFFFFFFF
            # Calculate how long it took to generate this packet.
            # Then how long we should wait to send the next, then devide by 2.
            rate = self.preference.rate if self.preference.rate > 0 else 8000
            delay = (1 / rate) * 160
            sleep_time = max(
                0, delay - ((time.monotonic_ns() - last_sent) / 1000000000)
            )
            time.sleep(sleep_time / self.trans_delay_reduction)

    @property
    def trans_delay_reduction(self) -> float:
        reduction = pyVoIP.TRANSMIT_DELAY_REDUCTION + 1
        return reduction if reduction else 1.0

    def parsePacket(self, packet: bytes) -> None:
        warnings.warn(
            "parsePacket is deprecated due to PEP8 compliance. "
            + "Use parse_packet instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_packet(packet)

    def parse_packet(self, packet: bytes) -> None:
        msg = RTPMessage(packet, self.assoc)
        if msg.payload_type == PayloadType.PCMU:
            self.parse_pcmu(msg)
        elif msg.payload_type == PayloadType.PCMA:
            self.parse_pcma(msg)
        elif msg.payload_type == PayloadType.EVENT:
            self.parse_telephone_event(msg)
        else:
            raise RTPParseError(
                "Unsupported codec (parse): " + str(msg.payload_type)
            )

    def encodePacket(self, payload: bytes) -> bytes:
        warnings.warn(
            "encodePacket is deprecated due to PEP8 compliance. "
            + "Use encode_packet instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_packet(payload)

    def encode_packet(self, payload: bytes) -> bytes:
        if self.preference == PayloadType.PCMU:
            return self.encode_pcmu(payload)
        elif self.preference == PayloadType.PCMA:
            return self.encode_pcma(payload)
        else:
            raise RTPParseError(
                "Unsupported codec (encode): " + str(self.preference)
            )

    def parsePCMU(self, packet: RTPMessage) -> None:
        warnings.warn(
            "parsePCMU is deprecated due to PEP8 compliance. "
            + "Use parse_pcmu instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_pcmu(packet)

    def parse_pcmu(self, packet: RTPMessage) -> None:
        data = audioop.ulaw2lin(packet.payload, 1)
        data = audioop.bias(data, 1, 128)
        self.pmin.write(packet.timestamp, data)

    def encodePCMU(self, packet: bytes) -> bytes:
        warnings.warn(
            "encodePCMU is deprecated due to PEP8 compliance. "
            + "Use encode_pcmu instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_pcmu(packet)

    def encode_pcmu(self, packet: bytes) -> bytes:
        packet = audioop.bias(packet, 1, -128)
        packet = audioop.lin2ulaw(packet, 1)
        return packet

    def parsePCMA(self, packet: RTPMessage) -> None:
        warnings.warn(
            "parsePCMA is deprecated due to PEP8 compliance. "
            + "Use parse_pcma instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_pcma(packet)

    def parse_pcma(self, packet: RTPMessage) -> None:
        data = audioop.alaw2lin(packet.payload, 1)
        data = audioop.bias(data, 1, 128)
        self.pmin.write(packet.timestamp, data)

    def encodePCMA(self, packet: bytes) -> bytes:
        warnings.warn(
            "encodePCMA is deprecated due to PEP8 compliance. "
            + "Use encode_pcma instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.encode_pcma(packet)

    def encode_pcma(self, packet: bytes) -> bytes:
        packet = audioop.bias(packet, 1, -128)
        packet = audioop.lin2alaw(packet, 1)
        return packet

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
        key = [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "*",
            "#",
            "A",
            "B",
            "C",
            "D",
        ]

        payload = packet.payload
        event = key[payload[0]]
        """
        Commented out the following due to F841 (Unused variable).
        Might use at some point though, so I'm saving the logic.

        byte = byte_to_bits(payload[1:2])
        end = (byte[0] == '1')
        volume = int(byte[2:], 2)
        """

        if packet.marker:
            if self.dtmf is not None:
                self.dtmf(event)
