from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CodecAvailability:
    available: bool
    reason: str = ""
    library: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "available": self.available,
            "reason": self.reason,
            "library": self.library,
        }


class RTPCodec:
    """Runtime codec implementation.

    PyVoIP's public audio API currently reads/writes unsigned 8-bit, 8 kHz,
    mono samples. Codecs with a different RTP clock, such as Opus, convert to
    and from that public format internally.
    """

    name = ""
    description = ""
    payload_kind = "audio"
    rate = 8000
    channels = 1
    rtpmap_channels: Optional[int] = None
    default_payload_type: Optional[int] = None
    dynamic = False
    can_transmit_audio = True
    priority_score = 0
    frame_duration_ms = 20
    source_sample_rate = 8000
    source_sample_width = 1
    source_channels = 1
    default_fmtp: List[str] = []

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in")

    @classmethod
    def rtpmap(cls, payload_type: int) -> str:
        channels = cls.rtpmap_channels
        channel_suffix = ""
        if channels not in (None, 0, 1):
            channel_suffix = f"/{channels}"
        return f"{payload_type} {cls.name}/{cls.rate}{channel_suffix}"

    @classmethod
    def fmtp(cls) -> List[str]:
        return list(cls.default_fmtp)

    @classmethod
    def fmtp_supported(cls, fmtp: List[str]) -> bool:
        return True

    def packet_duration_seconds(self, source_payload: bytes) -> float:
        bytes_per_second = (
            self.source_sample_rate
            * self.source_sample_width
            * self.source_channels
        )
        if bytes_per_second <= 0:
            return self.frame_duration_ms / 1000.0
        return max(0.001, len(source_payload) / bytes_per_second)

    def rtp_timestamp_increment(
        self,
        source_payload: bytes,
        encoded_payload: bytes,
    ) -> int:
        return max(
            1,
            int(round(self.packet_duration_seconds(source_payload) * self.rate)),
        )

    def output_offset(self, timestamp: int) -> int:
        if self.rate == self.source_sample_rate:
            return timestamp
        return int((timestamp * self.source_sample_rate) / self.rate)

    def encode(self, payload: bytes) -> bytes:
        raise NotImplementedError

    def decode(self, payload: bytes) -> bytes:
        raise NotImplementedError
