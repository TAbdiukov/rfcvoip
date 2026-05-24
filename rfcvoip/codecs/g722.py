from dataclasses import dataclass, field
from typing import List

from pyVoIP.codecs.base import CodecAvailability, RTPCodec


_AUDIO_SAMPLE_RATE = 16000
_RTP_CLOCK_RATE = 8000

_Q6 = (
    0, 35, 72, 110, 150, 190, 233, 276,
    323, 370, 422, 473, 530, 587, 650, 714,
    786, 858, 940, 1023, 1121, 1219, 1339, 1458,
    1612, 1765, 1980, 2195, 2557, 2919, 0, 0,
)
_ILN = (
    0, 63, 62, 31, 30, 29, 28, 27,
    26, 25, 24, 23, 22, 21, 20, 19,
    18, 17, 16, 15, 14, 13, 12, 11,
    10, 9, 8, 7, 6, 5, 4, 0,
)
_ILP = (
    0, 61, 60, 59, 58, 57, 56, 55,
    54, 53, 52, 51, 50, 49, 48, 47,
    46, 45, 44, 43, 42, 41, 40, 39,
    38, 37, 36, 35, 34, 33, 32, 0,
)
_WL = (-60, -30, 58, 172, 334, 538, 1198, 3042)
_RL42 = (0, 7, 6, 5, 4, 3, 2, 1, 7, 6, 5, 4, 3, 2, 1, 0)
_ILB = (
    2048, 2093, 2139, 2186, 2233, 2282, 2332, 2383,
    2435, 2489, 2543, 2599, 2656, 2714, 2774, 2834,
    2896, 2960, 3025, 3091, 3158, 3228, 3298, 3371,
    3444, 3520, 3597, 3676, 3756, 3838, 3922, 4008,
)
_QM2 = (-7408, -1616, 7408, 1616)
_QM4 = (
    0, -20456, -12896, -8968,
    -6288, -4240, -2584, -1200,
    20456, 12896, 8968, 6288,
    4240, 2584, 1200, 0,
)
_QM6 = (
    -136, -136, -136, -136,
    -24808, -21904, -19008, -16704,
    -14984, -13512, -12280, -11192,
    -10232, -9360, -8576, -7856,
    -7192, -6576, -6000, -5456,
    -4944, -4464, -4008, -3576,
    -3168, -2776, -2400, -2032,
    -1688, -1360, -1040, -728,
    24808, 21904, 19008, 16704,
    14984, 13512, 12280, 11192,
    10232, 9360, 8576, 7856,
    7192, 6576, 6000, 5456,
    4944, 4464, 4008, 3576,
    3168, 2776, 2400, 2032,
    1688, 1360, 1040, 728,
    432, 136, -432, -136,
)
_QMF_COEFFS = (3, -11, 12, 32, -210, 951, 3876, -805, 362, -156, 53, -11)
_IHN = (0, 1, 0)
_IHP = (0, 3, 2)
_WH = (0, -214, 798)
_RH2 = (2, 1, 2, 1)


def _saturate_16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return int(value)


@dataclass
class _G722Band:
    s: int = 0
    sp: int = 0
    sz: int = 0
    nb: int = 0
    det: int = 0
    r: List[int] = field(default_factory=lambda: [0] * 3)
    a: List[int] = field(default_factory=lambda: [0] * 3)
    ap: List[int] = field(default_factory=lambda: [0] * 3)
    p: List[int] = field(default_factory=lambda: [0] * 3)
    d: List[int] = field(default_factory=lambda: [0] * 7)
    b: List[int] = field(default_factory=lambda: [0] * 7)
    bp: List[int] = field(default_factory=lambda: [0] * 7)
    sg: List[int] = field(default_factory=lambda: [0] * 7)


def _block4(band: _G722Band, d: int) -> None:
    """Run the common G.722 adaptive predictor update block."""
    band.d[0] = d
    band.r[0] = _saturate_16(band.s + d)
    band.p[0] = _saturate_16(band.sz + d)

    for i in range(3):
        band.sg[i] = band.p[i] >> 15

    wd1 = _saturate_16(band.a[1] << 2)
    wd2 = -wd1 if band.sg[0] == band.sg[1] else wd1
    if wd2 > 32767:
        wd2 = 32767

    wd3 = (wd2 >> 7) + (128 if band.sg[0] == band.sg[2] else -128)
    wd3 += (band.a[2] * 32512) >> 15
    if wd3 > 12288:
        wd3 = 12288
    elif wd3 < -12288:
        wd3 = -12288
    band.ap[2] = wd3

    band.sg[0] = band.p[0] >> 15
    band.sg[1] = band.p[1] >> 15
    wd1 = 192 if band.sg[0] == band.sg[1] else -192
    wd2 = (band.a[1] * 32640) >> 15
    band.ap[1] = _saturate_16(wd1 + wd2)
    wd3 = _saturate_16(15360 - band.ap[2])
    if band.ap[1] > wd3:
        band.ap[1] = wd3
    elif band.ap[1] < -wd3:
        band.ap[1] = -wd3

    wd1 = 0 if d == 0 else 128
    band.sg[0] = d >> 15
    for i in range(1, 7):
        band.sg[i] = band.d[i] >> 15
        wd2 = wd1 if band.sg[i] == band.sg[0] else -wd1
        wd3 = (band.b[i] * 32640) >> 15
        band.bp[i] = _saturate_16(wd2 + wd3)

    for i in range(6, 0, -1):
        band.d[i] = band.d[i - 1]
        band.b[i] = band.bp[i]

    for i in range(2, 0, -1):
        band.r[i] = band.r[i - 1]
        band.p[i] = band.p[i - 1]
        band.a[i] = band.ap[i]

    wd1 = _saturate_16(band.r[1] + band.r[1])
    wd1 = (band.a[1] * wd1) >> 15
    wd2 = _saturate_16(band.r[2] + band.r[2])
    wd2 = (band.a[2] * wd2) >> 15
    band.sp = _saturate_16(wd1 + wd2)

    band.sz = 0
    for i in range(6, 0, -1):
        wd1 = _saturate_16(band.d[i] + band.d[i])
        band.sz += (band.b[i] * wd1) >> 15
    band.sz = _saturate_16(band.sz)
    band.s = _saturate_16(band.sp + band.sz)


class _G722State:
    def __init__(self) -> None:
        self.band = [_G722Band(det=32), _G722Band(det=8)]
        self.x = [0] * 24

    def _update_low_band(self, code_index: int, difference: int) -> None:
        il4 = _RL42[code_index]
        nb = ((self.band[0].nb * 127) >> 7) + _WL[il4]
        if nb < 0:
            nb = 0
        elif nb > 18432:
            nb = 18432
        self.band[0].nb = nb

        wd1 = (nb >> 6) & 31
        wd2 = 8 - (nb >> 11)
        wd3 = _ILB[wd1] << -wd2 if wd2 < 0 else _ILB[wd1] >> wd2
        self.band[0].det = wd3 << 2
        _block4(self.band[0], difference)

    def _update_high_band(self, ihigh: int, difference: int) -> None:
        ih2 = _RH2[ihigh]
        nb = ((self.band[1].nb * 127) >> 7) + _WH[ih2]
        if nb < 0:
            nb = 0
        elif nb > 22528:
            nb = 22528
        self.band[1].nb = nb

        wd1 = (nb >> 6) & 31
        wd2 = 10 - (nb >> 11)
        wd3 = _ILB[wd1] << -wd2 if wd2 < 0 else _ILB[wd1] >> wd2
        self.band[1].det = wd3 << 2
        _block4(self.band[1], difference)


class _G722Encoder(_G722State):
    def encode_samples(self, samples: List[int]) -> bytes:
        if len(samples) % 2:
            samples.append(0)

        encoded = bytearray()
        for pos in range(0, len(samples), 2):
            for i in range(22):
                self.x[i] = self.x[i + 2]
            self.x[22] = int(samples[pos])
            self.x[23] = int(samples[pos + 1])

            sum_odd = 0
            sum_even = 0
            for i in range(12):
                sum_odd += self.x[2 * i] * _QMF_COEFFS[i]
                sum_even += self.x[2 * i + 1] * _QMF_COEFFS[11 - i]
            xlow = (sum_even + sum_odd) >> 14
            xhigh = (sum_even - sum_odd) >> 14

            el = _saturate_16(xlow - self.band[0].s)
            wd = el if el >= 0 else -(el + 1)
            i = 1
            while i < 30:
                wd1 = (_Q6[i] * self.band[0].det) >> 12
                if wd < wd1:
                    break
                i += 1
            ilow = _ILN[i] if el < 0 else _ILP[i]

            ril = ilow >> 2
            dlow = (self.band[0].det * _QM4[ril]) >> 15
            self._update_low_band(ril, dlow)

            eh = _saturate_16(xhigh - self.band[1].s)
            wd = eh if eh >= 0 else -(eh + 1)
            threshold = (564 * self.band[1].det) >> 12
            mih = 2 if wd >= threshold else 1
            ihigh = _IHN[mih] if eh < 0 else _IHP[mih]

            dhigh = (self.band[1].det * _QM2[ihigh]) >> 15
            self._update_high_band(ihigh, dhigh)

            encoded.append(((ihigh << 6) | ilow) & 0xFF)

        return bytes(encoded)


class _G722Decoder(_G722State):
    def decode_bytes(self, data: bytes) -> bytes:
        decoded = bytearray()
        for code in data:
            wd1 = code & 0x3F
            ihigh = (code >> 6) & 0x03
            wd2 = _QM6[wd1]
            wd1 >>= 2

            wd2 = (self.band[0].det * wd2) >> 15
            rlow = self.band[0].s + wd2
            if rlow > 16383:
                rlow = 16383
            elif rlow < -16384:
                rlow = -16384

            dlow = (self.band[0].det * _QM4[wd1]) >> 15
            self._update_low_band(wd1, dlow)

            dhigh = (self.band[1].det * _QM2[ihigh]) >> 15
            rhigh = dhigh + self.band[1].s
            if rhigh > 16383:
                rhigh = 16383
            elif rhigh < -16384:
                rhigh = -16384

            self._update_high_band(ihigh, dhigh)

            for i in range(22):
                self.x[i] = self.x[i + 2]
            self.x[22] = rlow + rhigh
            self.x[23] = rlow - rhigh

            xout1 = 0
            xout2 = 0
            for i in range(12):
                xout2 += self.x[2 * i] * _QMF_COEFFS[i]
                xout1 += self.x[2 * i + 1] * _QMF_COEFFS[11 - i]

            for sample in (
                _saturate_16(xout1 >> 11),
                _saturate_16(xout2 >> 11),
            ):
                decoded.extend(int(sample).to_bytes(2, "little", signed=True))

        return bytes(decoded)


class G722Codec(RTPCodec):
    """ITU-T G.722 64 kbit/s wideband codec for RTP payload type 9.

    RTP names this static payload ``G722/8000`` for compatibility even though
    the encoded linear audio signal is 16 kHz wideband audio.  The public
    PyVoIP byte stream remains unsigned 8-bit mono and is resampled to/from
    16 kHz internally.
    """

    payload_type = "G722"
    name = "G722"
    description = "G722"
    rate = _RTP_CLOCK_RATE
    channels = 1
    default_payload_type = 9
    priority_score = 825
    preferred_source_sample_rate = _AUDIO_SAMPLE_RATE
    source_sample_rate = _AUDIO_SAMPLE_RATE
    required_bandwidth_bps = 64000
    frame_duration_ms = 20

    @classmethod
    def availability(cls) -> CodecAvailability:
        return CodecAvailability(True, "built-in G.722 SB-ADPCM")

    def __init__(self) -> None:
        self._encoder = _G722Encoder()
        self._decoder = _G722Decoder()
        self._encode_rate_state = None
        self._decode_rate_state = None

    def encode(self, payload: bytes) -> bytes:
        if not payload:
            payload = b"\x80" * self.source_frame_size()

        pcm16 = self._source_u8_to_pcm16(payload, _AUDIO_SAMPLE_RATE)
        samples = [
            int.from_bytes(pcm16[i : i + 2], "little", signed=True)
            for i in range(0, len(pcm16) - 1, 2)
        ]
        return self._encoder.encode_samples(samples)

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return b"\x80" * self.source_frame_size()

        pcm16 = self._decoder.decode_bytes(payload)
        decoded = self._pcm16_to_source_u8(pcm16, _AUDIO_SAMPLE_RATE)
        expected_length = max(
            1,
            int(
                round(
                    (len(payload) / _RTP_CLOCK_RATE)
                    * self.source_sample_rate
                )
            ),
        )
        return self._fit_bytes(decoded, expected_length, b"\x80")
