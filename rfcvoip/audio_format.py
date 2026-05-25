from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Union


VALID_PUBLIC_AUDIO_BIT_DEPTHS = {8, 16, 24, 32, 64}
AUTO_PUBLIC_AUDIO_BIT_DEPTH = "best"


def normalize_audio_bit_depth(value: Any) -> Union[int, str]:
    """Normalize a public audio bit-depth option.

    Accepted fixed depths are 8, 16, 24, 32, and 64.  The string ``"best"``
    asks rfcvoip to choose a codec-preferred public bit depth after codec
    negotiation.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text == AUTO_PUBLIC_AUDIO_BIT_DEPTH:
            return AUTO_PUBLIC_AUDIO_BIT_DEPTH
        if not text.isdigit():
            raise ValueError(
                "audio_bit_depth must be 8, 16, 24, 32, 64, or 'best'."
            )
        value = int(text)
    elif not isinstance(value, int):
        raise ValueError(
            "audio_bit_depth must be 8, 16, 24, 32, 64, or 'best'."
        )

    if value not in VALID_PUBLIC_AUDIO_BIT_DEPTHS:
        raise ValueError(
            "audio_bit_depth must be 8, 16, 24, 32, 64, or 'best'."
        )
    return int(value)


def _fixed_bit_depth(value: Any) -> int:
    bit_depth = normalize_audio_bit_depth(value)
    if bit_depth == AUTO_PUBLIC_AUDIO_BIT_DEPTH:
        raise ValueError("'best' must be resolved before PCM conversion.")
    return int(bit_depth)


def sample_width_bytes(bit_depth: int) -> int:
    return _fixed_bit_depth(bit_depth) // 8


def public_sample_format(bit_depth: int) -> str:
    bit_depth = _fixed_bit_depth(bit_depth)
    if bit_depth == 8:
        return "u8"
    return f"s{bit_depth}le"


def public_sample_signed(bit_depth: int) -> bool:
    return _fixed_bit_depth(bit_depth) != 8


def public_sample_endian(bit_depth: int):
    return None if _fixed_bit_depth(bit_depth) == 8 else "little"


def preferred_public_bit_depth(codec: Any, fallback: int = 8) -> int:
    """Return a codec's preferred public bit depth, or ``fallback``."""
    try:
        from rfcvoip.codecs import codec_class

        cls = codec_class(codec)
        if cls is not None:
            return _fixed_bit_depth(
                getattr(cls, "preferred_public_bit_depth", fallback)
            )
    except Exception:
        pass

    try:
        return _fixed_bit_depth(getattr(codec, "preferred_public_bit_depth"))
    except Exception:
        return _fixed_bit_depth(fallback)


def resolve_audio_bit_depth(
    value: Any,
    *,
    codec: Any = None,
    fallback: int = 8,
) -> int:
    bit_depth = normalize_audio_bit_depth(value)
    if bit_depth == AUTO_PUBLIC_AUDIO_BIT_DEPTH:
        return preferred_public_bit_depth(codec, fallback=fallback)
    return int(bit_depth)


def _trim_complete_samples(data: bytes, width: int) -> bytes:
    data = bytes(data or b"")
    usable = len(data) - (len(data) % width)
    return data[:usable]


def _clamp_int16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def _int16le(value: int) -> bytes:
    return _clamp_int16(value).to_bytes(2, "little", signed=True)


def public_pcm_to_s16le(data: bytes, bit_depth: int) -> bytes:
    """Convert public linear PCM bytes to signed 16-bit little-endian PCM."""
    bit_depth = _fixed_bit_depth(bit_depth)
    width = sample_width_bytes(bit_depth)
    data = _trim_complete_samples(data, width)

    if bit_depth == 16:
        return data

    out = bytearray()
    if bit_depth == 8:
        for sample in data:
            out.extend(_int16le((int(sample) - 128) << 8))
        return bytes(out)

    for offset in range(0, len(data), width):
        sample = data[offset : offset + width]
        if bit_depth == 24:
            value = sample[0] | (sample[1] << 8) | (sample[2] << 16)
            if value & 0x800000:
                value -= 1 << 24
            out.extend(_int16le(value >> 8))
        elif bit_depth == 32:
            value = int.from_bytes(sample, "little", signed=True)
            out.extend(_int16le(value >> 16))
        else:
            value = int.from_bytes(sample, "little", signed=True)
            out.extend(_int16le(value >> 48))
    return bytes(out)


def s16le_to_public_pcm(data: bytes, bit_depth: int) -> bytes:
    """Convert signed 16-bit little-endian PCM to public linear PCM bytes."""
    bit_depth = _fixed_bit_depth(bit_depth)
    data = _trim_complete_samples(data, 2)

    if bit_depth == 16:
        return data

    out = bytearray()
    for offset in range(0, len(data), 2):
        sample = int.from_bytes(data[offset : offset + 2], "little", signed=True)
        if bit_depth == 8:
            out.append(max(0, min(255, (sample + 32768) >> 8)))
        elif bit_depth == 24:
            value = (sample << 8) & 0xFFFFFF
            out.extend(
                bytes(
                    (
                        value & 0xFF,
                        (value >> 8) & 0xFF,
                        (value >> 16) & 0xFF,
                    )
                )
            )
        elif bit_depth == 32:
            out.extend((sample << 16).to_bytes(4, "little", signed=True))
        else:
            out.extend((sample << 48).to_bytes(8, "little", signed=True))
    return bytes(out)


def silence_bytes(length: int, bit_depth: int) -> bytes:
    bit_depth = _fixed_bit_depth(bit_depth)
    length = max(0, int(length))
    if bit_depth == 8:
        return b"\x80" * length
    return b"\x00" * length


@dataclass(frozen=True)
class PublicAudioFormat:
    sample_rate: int
    channels: int
    bit_depth: int
    frame_ms: int = 20

    def __post_init__(self) -> None:
        sample_rate = int(self.sample_rate)
        channels = int(self.channels)
        frame_ms = int(self.frame_ms)
        bit_depth = _fixed_bit_depth(self.bit_depth)

        if sample_rate <= 0:
            raise ValueError("Audio sample rate must be positive.")
        if channels <= 0:
            raise ValueError("Audio channel count must be positive.")
        if frame_ms <= 0:
            raise ValueError("Audio frame duration must be positive.")

        object.__setattr__(self, "sample_rate", sample_rate)
        object.__setattr__(self, "channels", channels)
        object.__setattr__(self, "bit_depth", bit_depth)
        object.__setattr__(self, "frame_ms", frame_ms)

    @property
    def bits_per_sample(self) -> int:
        return self.bit_depth

    @property
    def sample_width(self) -> int:
        return sample_width_bytes(self.bit_depth)

    @property
    def sample_width_bytes(self) -> int:
        return self.sample_width

    @property
    def sample_format(self) -> str:
        return public_sample_format(self.bit_depth)

    @property
    def signed(self) -> bool:
        return public_sample_signed(self.bit_depth)

    @property
    def endian(self):
        return public_sample_endian(self.bit_depth)

    @property
    def frame_size(self) -> int:
        samples = int(round(self.sample_rate * (self.frame_ms / 1000.0)))
        return max(1, samples * self.channels * self.sample_width)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bit_depth": self.bit_depth,
            "bits_per_sample": self.bits_per_sample,
            "sample_width": self.sample_width,
            "sample_width_bytes": self.sample_width_bytes,
            "encoding": "linear_pcm",
            "sample_format": self.sample_format,
            "signed": self.signed,
            "endian": self.endian,
            "frame_ms": self.frame_ms,
            "frame_size": self.frame_size,
        }