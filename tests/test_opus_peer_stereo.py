import ctypes
import math
import struct

import pytest

from rfcvoip.codecs.opus import OpusCodec


def stereo_pcm():
    return b"".join(
        struct.pack(
            "<hh",
            int(12000 * math.sin(index * 2 * math.pi * 440 / 48000)),
            int(12000 * math.sin(index * 2 * math.pi * 880 / 48000)),
        )
        for index in range(960)
    )


def packet_channels(codec, payload):
    get_channels = codec._lib.opus_packet_get_nb_channels
    get_channels.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    get_channels.restype = ctypes.c_int
    packet = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    return get_channels(packet)


@pytest.mark.parametrize(
    ("settings", "expected_channels"),
    [(None, 1), ([], 1), (["stereo=0"], 1), (["stereo=1"], 2)],
)
def test_real_opus_encoder_obeys_peer_stereo_preference(
    settings, expected_channels
):
    if not OpusCodec.availability().available:
        pytest.skip("optional libopus is not installed")
    codec = OpusCodec()
    codec.configure_source_format(sample_rate=48000, bit_depth=16, channels=2)
    if settings is not None:
        codec.configure_fmtp(settings)

    payload = codec.encode(stereo_pcm())

    assert packet_channels(codec, payload) == expected_channels
    # Public audio and the decoder remain stereo even when sending mono.
    assert codec.source_channels == 2
    assert len(codec.decode(payload)) == 3840
    assert codec.rtpmap(111) == "111 opus/48000/2"


def test_real_opus_encoder_can_switch_back_to_mono_without_changing_decoder():
    if not OpusCodec.availability().available:
        pytest.skip("optional libopus is not installed")
    codec = OpusCodec()
    codec.configure_source_format(sample_rate=48000, bit_depth=16, channels=2)
    codec.configure_fmtp(["stereo=1"])
    stereo_payload = codec.encode(stereo_pcm())
    assert packet_channels(codec, stereo_payload) == 2
    assert len(codec.decode(stereo_payload)) == 3840

    codec.configure_fmtp(["useinbandfec=1; stereo=0"])
    mono_payload = codec.encode(stereo_pcm())

    assert packet_channels(codec, mono_payload) == 1
    assert len(codec.decode(mono_payload)) == 3840
