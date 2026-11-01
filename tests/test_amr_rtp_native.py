"""Exercise the complete RTP send/receive path with the real speech libraries."""

import math
import os
import struct

import pytest

from rfcvoip import RTP
from rfcvoip.codecs import codec_class


@pytest.mark.parametrize(
    "codec", (RTP.PayloadType.AMR, RTP.PayloadType.AMR_WB)
)
@pytest.mark.parametrize("octet_align", (0, 1))
def test_native_rtp_remapped_payload_audio_and_mode_requests(
    codec, octet_align
):
    availability = codec_class(codec).availability()
    if not availability.available:
        if os.environ.get("RFCVOIP_REQUIRE_AMR") == "1":
            pytest.fail(availability.reason)
        pytest.skip(availability.reason)
    settings = [f"octet-align={octet_align};mode-set=0,2"]
    client = RTP.RTPClient(
        {96: codec},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40000,
        RTP.TransmitType.SENDRECV,
        audio_bit_depth="best",
        receive_assoc={118: codec},
        codec_fmtp={96: settings},
        receive_codec_fmtp={118: settings},
    )
    samples = codec.rate // 50
    assert client.audio_sample_rate == codec.rate
    assert client.audio_bit_depth == 16
    source = struct.pack(
        "<" + "h" * samples,
        *(
            round(12000 * math.sin(2 * math.pi * 440 * index / codec.rate))
            for index in range(samples)
        ),
    )
    pcm = []
    try:
        for index in range(10):
            payload = client.encode_packet(source)
            packet = struct.pack(
                "!BBHII", 0x80, 118, index, index * samples, 1
            )
            client.parse_packet(packet + payload)
            assert client.pmin.available() == len(source)
            pcm.append(client.read(len(source), blocking=False))
        assert any(b"".join(pcm))

        # A valid incoming CMR reaches the opposite-direction encoder even
        # though receive PT 118 and transmit PT 96 differ.
        payload = client.encode_packet(source)
        payload = bytes([payload[0] & 0x0F]) + payload[1:]
        packet = struct.pack("!BBHII", 0x80, 118, 10, 10 * samples, 1)
        client.parse_packet(packet + payload)
        next_payload = client.encode_packet(source)
        # FT 0 occupies bits 6..3 in OA or bits 2..0 + bit 7 in BE.
        frame_type = (
            (next_payload[1] >> 3) & 15
            if octet_align
            else ((next_payload[0] & 7) << 1) | (next_payload[1] >> 7)
        )
        assert frame_type == 0
        assert client.preference_payload_type == 96

        available = client.pmin.available()
        with pytest.raises(RTP.RTPParseError, match="decode failed"):
            client.parse_packet(
                struct.pack("!BBHII", 0x80, 118, 11, 11 * samples, 1)
                + next_payload[:-1]
            )
        assert client.pmin.available() == available
    finally:
        for adapter in list(client._codec_adapters.values()) + list(
            client._receive_codec_adapters.values()
        ):
            adapter.close()
