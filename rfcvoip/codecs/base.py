import audioop
from dataclasses import dataclass
from typing import Dict, List, Optional

from rfcvoip.audio_format import (
    normalize_audio_bit_depth,
    public_pcm_to_s16le,
    s16le_to_public_pcm,
    sample_width_bytes,
    silence_bytes,
)


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

    rfcvoip's public audio API reads/writes linear PCM bytes.  The sample
    rate, channel count, and bit depth are configurable per RTP client.
    Codecs convert between that public format and their RTP/native clock
    internally.
    """

    payload_type = None
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
    preferred_public_bit_depth = 8
    preferred_source_sample_rate = 8000
    source_sample_rate = 8000
    source_sample_width = 1
    source_bit_depth = 8
    source_channels = 1
    default_fmtp: List[str] = []
    required_bandwidth_bps: Optional[int] = None
    extra_package: Optional[str] = None
    package_extra: Optional[str] = None
    requires_extra_package: bool = False
    requires_compiler: bool = False

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

    def configure_source_format(
        self,
        *,
        sample_rate: Optional[int] = None,
        sample_width: int = 1,
        channels: int = 1,
        bit_depth: Optional[int] = None,
    ) -> None:
        """Configure rfcvoip's public audio format for this codec instance.

        The default public byte stream remains unsigned 8-bit for compatibility
        with existing callers.  Mono and stereo are supported.
        """
        if sample_rate is None:
            sample_rate = int(self.preferred_source_sample_rate)

        try:
            sample_rate = int(sample_rate)
            channels = int(channels)
        except (TypeError, ValueError) as ex:
            raise ValueError("Audio format values must be integers.") from ex

        if bit_depth is None:
            try:
                bit_depth = int(sample_width) * 8
            except (TypeError, ValueError) as ex:
                raise ValueError("Audio format values must be integers.") from ex

        try:
            bit_depth = normalize_audio_bit_depth(bit_depth)
        except ValueError as ex:
            raise ValueError(str(ex)) from ex
        if bit_depth == "best":
            raise ValueError("Codec audio bit depth must be resolved before use.")

        if sample_rate <= 0:
            raise ValueError("Audio sample rate must be positive.")
        if channels not in (1, 2):
            raise ValueError(
                "rfcvoip public audio currently supports mono or stereo only."
            )

        self.source_sample_rate = sample_rate
        self.source_bit_depth = int(bit_depth)
        self.source_sample_width = sample_width_bytes(self.source_bit_depth)
        self.source_channels = channels
        self._encode_rate_state = None
        self._decode_rate_state = None

    def source_frame_size(self, duration_ms: Optional[int] = None) -> int:
        duration_ms = (
            self.frame_duration_ms if duration_ms is None else duration_ms
        )
        return max(
            1,
            int(
                round(
                    self.source_sample_rate
                    * self.source_sample_width
                    * self.source_channels
                    * (duration_ms / 1000.0)
                )
            ),
        )

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
        samples = timestamp
        if self.rate != self.source_sample_rate:
            samples = int((timestamp * self.source_sample_rate) / self.rate)
        return samples * self.source_sample_width * self.source_channels

    @staticmethod
    def _convert_pcm16_channels(
        pcm16: bytes,
        source_channels: int,
        target_channels: int,
    ) -> bytes:
        if source_channels not in (1, 2) or target_channels not in (1, 2):
            raise ValueError("Only mono and stereo PCM16 conversion is supported.")
        if source_channels == target_channels:
            return pcm16
        if source_channels == 1 and target_channels == 2:
            return audioop.tostereo(pcm16, 2, 1.0, 1.0)
        return audioop.tomono(pcm16, 2, 0.5, 0.5)

    def _source_u8_to_pcm16(
        self,
        payload: bytes,
        target_rate: int,
        target_channels: Optional[int] = None,
    ) -> bytes:
        """Convert public linear PCM audio to signed 16-bit PCM."""
        if not payload:
            payload = silence_bytes(
                self.source_frame_size(),
                self.source_bit_depth,
            )

        target_channels = (
            self.channels if target_channels is None else int(target_channels)
        )
        pcm16 = public_pcm_to_s16le(payload, self.source_bit_depth)
        if not pcm16:
            pcm16 = public_pcm_to_s16le(
                silence_bytes(
                    self.source_frame_size(),
                    self.source_bit_depth,
                ),
                self.source_bit_depth,
            )
        pcm16 = self._convert_pcm16_channels(
            pcm16,
            self.source_channels,
            target_channels,
        )

        source_rate = int(self.source_sample_rate)
        target_rate = int(target_rate)
        if source_rate != target_rate:
            pcm16, state = audioop.ratecv(
                pcm16,
                2,
                target_channels,
                source_rate,
                target_rate,
                getattr(self, "_encode_rate_state", None),
            )
            self._encode_rate_state = state

        return pcm16

    def _pcm16_to_source_u8(
        self,
        pcm16: bytes,
        source_rate: int,
        source_channels: Optional[int] = None,
    ) -> bytes:
        """Convert signed 16-bit PCM to public linear PCM audio."""
        if not pcm16:
            return silence_bytes(
                self.source_frame_size(),
                self.source_bit_depth,
            )

        source_channels = (
            self.channels if source_channels is None else int(source_channels)
        )
        source_rate = int(source_rate)
        target_rate = int(self.source_sample_rate)
        if source_rate != target_rate:
            pcm16, state = audioop.ratecv(
                pcm16,
                2,
                source_channels,
                source_rate,
                target_rate,
                getattr(self, "_decode_rate_state", None),
            )
            self._decode_rate_state = state

        pcm16 = self._convert_pcm16_channels(
            pcm16,
            source_channels,
            self.source_channels,
        )
        return s16le_to_public_pcm(pcm16, self.source_bit_depth)

    @staticmethod
    def _fit_bytes(data: bytes, length: int, pad: bytes) -> bytes:
        if len(data) >= length:
            return data[:length]
        return data + (pad * (length - len(data)))

    def encode(self, payload: bytes) -> bytes:
        raise NotImplementedError

    def decode(self, payload: bytes) -> bytes:
        raise NotImplementedError
