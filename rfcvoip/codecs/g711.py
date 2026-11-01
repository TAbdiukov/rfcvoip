from abc import ABC, abstractmethod
import audioop
import math

from rfcvoip.audio_format import silence_bytes
from rfcvoip.codecs.base import (
    CodecAvailability,
    CodecNotImplementedError,
    RTPCodec,
)


class PCMUCodec(RTPCodec):
    payload_type = "PCMU"
    name = "PCMU"
    description = "PCMU"
    rate = 8000
    channels = 1
    default_payload_type = 0
    priority_score = 700
    required_bandwidth_bps = 64000
    preferred_public_bit_depth = 8

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in audioop")

    def encode(self, payload: bytes) -> bytes:
        pcm16 = self._source_u8_to_pcm16(payload, self.rate)
        return audioop.lin2ulaw(pcm16, 2)

    def decode(self, payload: bytes) -> bytes:
        pcm16 = audioop.ulaw2lin(payload, 2)
        return self._pcm16_to_source_u8(pcm16, self.rate)

    def rtp_timestamp_increment(
        self, source_payload: bytes, encoded_payload: bytes
    ) -> int:
        if encoded_payload:
            return len(encoded_payload)
        return super().rtp_timestamp_increment(source_payload, encoded_payload)


class PCMACodec(RTPCodec):
    payload_type = "PCMA"
    name = "PCMA"
    description = "PCMA"
    rate = 8000
    channels = 1
    default_payload_type = 8
    priority_score = 650
    required_bandwidth_bps = 64000
    preferred_public_bit_depth = 8

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in audioop")

    def encode(self, payload: bytes) -> bytes:
        pcm16 = self._source_u8_to_pcm16(payload, self.rate)
        return audioop.lin2alaw(pcm16, 2)

    def decode(self, payload: bytes) -> bytes:
        pcm16 = audioop.alaw2lin(payload, 2)
        return self._pcm16_to_source_u8(pcm16, self.rate)

    def rtp_timestamp_increment(
        self, source_payload: bytes, encoded_payload: bytes
    ) -> int:
        if encoded_payload:
            return len(encoded_payload)
        return super().rtp_timestamp_increment(source_payload, encoded_payload)


class _G711WidebandCoreCodec(RTPCodec, ABC):
    """G.711.1 core-layer RTP codec adapter.

    ``PCMU-WB`` and ``PCMA-WB`` are the SDP/RTP names for G.711.1.  This
    adapter currently transmits only G.711.1 R1 (L0/core) frames and advertises
    ``mode-set=1``.  On receive, it extracts the L0 layer from any RFC 5391
    mode and ignores enhancement layers.  The public audio sample rate is still
    configurable; this adapter resamples between that public rate and the 8 kHz
    L0 core internally.
    """

    rate = 16000
    channels = 1
    dynamic = True
    preferred_source_sample_rate = 16000
    source_sample_rate = 16000
    default_fmtp = ["mode-set=1"]
    required_bandwidth_bps = 64000
    preferred_public_bit_depth = 16
    frame_duration_ms = 20
    core_sample_rate = 8000
    _mode_index = 1
    _core_frame_bytes = 40
    _core_frame_duration_ms = 5
    _core_pcm16_frame_bytes = _core_frame_bytes * 2
    _mode_frame_bytes = {
        1: 40,
        2: 50,
        3: 50,
        4: 60,
    }

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in G.711.1 R1 core via audioop")

    @classmethod
    def fmtp_supported(cls, fmtp) -> bool:
        text = " ".join(str(item) for item in (fmtp or []))
        text = text.replace(";", " ")
        mode_set = None
        for token in text.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key.strip().lower() == "mode-set":
                mode_set = value.strip()
                break

        if not mode_set:
            return True

        modes = {mode.strip() for mode in mode_set.split(",") if mode.strip()}
        return "1" in modes

    @abstractmethod
    def _encode_core_pcm16(self, payload: bytes) -> bytes:
        raise CodecNotImplementedError(self, "_encode_core_pcm16")

    @abstractmethod
    def _decode_core_pcm16(self, payload: bytes) -> bytes:
        raise CodecNotImplementedError(self, "_decode_core_pcm16")

    def _pad_to_core_frames(self, pcm16: bytes) -> bytes:
        if not pcm16:
            pcm16 = b"\x00" * self._core_pcm16_frame_bytes

        remainder = len(pcm16) % self._core_pcm16_frame_bytes
        if remainder:
            pcm16 += b"\x00" * (
                self._core_pcm16_frame_bytes - remainder
            )
        return pcm16

    def _encoding_core_frames(self, source_payload: bytes) -> int:
        source_bytes = len(source_payload) or self.source_frame_size()
        core_samples = source_bytes * self.core_sample_rate / (
            self.source_sample_rate
            * self.source_sample_width
            * self.source_channels
        )
        nearest = round(core_samples / self._core_frame_bytes)
        # Preserve a whole 5 ms duration when the public sample count merely
        # rounded by half a sample. Other durations pad to the next core frame.
        tolerance = self.core_sample_rate / (2 * self.source_sample_rate) + 1e-9
        if nearest > 0 and abs(
            core_samples - nearest * self._core_frame_bytes
        ) <= tolerance:
            return nearest
        return max(1, math.ceil(core_samples / self._core_frame_bytes))

    def packet_duration_seconds(self, source_payload: bytes) -> float:
        return (
            self._encoding_core_frames(source_payload)
            * self._core_frame_duration_ms / 1000.0
        )

    def rtp_timestamp_increment(
        self, source_payload: bytes, encoded_payload: bytes
    ) -> int:
        if encoded_payload:
            core_samples = len(encoded_payload) - 1
            return max(1, core_samples * self.rate // self.core_sample_rate)
        return super().rtp_timestamp_increment(source_payload, encoded_payload)

    def encode(self, payload: bytes) -> bytes:
        frames = self._encoding_core_frames(payload)
        pcm16_8k = self._source_u8_to_pcm16(payload, self.core_sample_rate)
        pcm16_8k = self._fit_bytes(
            pcm16_8k, frames * self._core_pcm16_frame_bytes, b"\x00"
        )
        pcm16_8k = self._pad_to_core_frames(pcm16_8k)
        core_payload = self._encode_core_pcm16(pcm16_8k)
        return bytes([self._mode_index]) + core_payload

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return silence_bytes(
                self.source_frame_size(),
                self.source_bit_depth,
            )

        mode_index = payload[0] & 0x07
        frame_size = self._mode_frame_bytes.get(mode_index)
        if frame_size is None:
            raise ValueError(f"Unsupported G.711.1 mode index {mode_index}")

        audio_data = payload[1:]
        core_payload = bytearray()
        frame_count = 0
        for offset in range(0, len(audio_data) - frame_size + 1, frame_size):
            frame = audio_data[offset : offset + frame_size]
            core_payload.extend(frame[: self._core_frame_bytes])
            frame_count += 1

        if not core_payload:
            return b""

        pcm16_8k = self._decode_core_pcm16(bytes(core_payload))
        decoded = self._pcm16_to_source_u8(pcm16_8k, self.core_sample_rate)
        expected_length = self.source_frame_size(
            self._core_frame_duration_ms * frame_count
        )
        pad = b"\x80" if self.source_bit_depth == 8 else b"\x00"
        return self._fit_bytes(decoded, expected_length, pad)


class PCMUWBCodec(_G711WidebandCoreCodec):
    payload_type = "PCMU-WB"
    name = "PCMU-WB"
    description = "PCMU-WB"
    default_payload_type = 112
    priority_score = 900

    def _encode_core_pcm16(self, payload: bytes) -> bytes:
        return audioop.lin2ulaw(payload, 2)

    def _decode_core_pcm16(self, payload: bytes) -> bytes:
        return audioop.ulaw2lin(payload, 2)


class PCMAWBCodec(_G711WidebandCoreCodec):
    payload_type = "PCMA-WB"
    name = "PCMA-WB"
    description = "PCMA-WB"
    default_payload_type = 113
    priority_score = 850

    def _encode_core_pcm16(self, payload: bytes) -> bytes:
        return audioop.lin2alaw(payload, 2)

    def _decode_core_pcm16(self, payload: bytes) -> bytes:
        return audioop.alaw2lin(payload, 2)
