import socket
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from rfcvoip import RTP
from rfcvoip.audio_format import PublicAudioFormat, silence_bytes
from rfcvoip.codecs import create_codec
from rfcvoip.codecs.g722 import G722Codec
from rfcvoip.util import acquired_lock_and_unblocked_socket


@pytest.mark.parametrize("bit_depth", [8, 16, 24, 32, 64])
def test_codec_frames_contain_complete_stereo_samples(bit_depth):
    codec = create_codec(
        RTP.PayloadType.PCMU,
        source_sample_rate=11025,
        source_bit_depth=bit_depth,
        source_channels=2,
    )
    public_format = PublicAudioFormat(11025, 2, bit_depth)

    assert codec.source_frame_size() == public_format.frame_size
    frame = silence_bytes(codec.source_frame_size(), bit_depth)
    assert codec.encode(frame)


def test_subsample_frame_duration_preserves_one_complete_sample():
    public_format = PublicAudioFormat(1, 2, 64, frame_ms=1)

    assert public_format.frame_size == 16


@pytest.mark.parametrize("bit_depth", [16, 24, 32, 64])
def test_wideband_decode_pads_signed_audio_with_zero(bit_depth):
    codec = create_codec(
        RTP.PayloadType.PCMU_WB,
        source_sample_rate=16000,
        source_bit_depth=bit_depth,
    )

    # The first rate conversion has one fewer sample than the target frame.
    decoded = codec.decode(b"\x01" + b"\xff" * 160)

    assert decoded == b"\x00" * codec.source_frame_size()


def test_g722_decode_returns_complete_stereo_samples():
    codec = G722Codec.__new__(G722Codec)
    codec.configure_source_format(sample_rate=11025, bit_depth=16, channels=2)
    codec._decoder = SimpleNamespace(decode=lambda payload: [0] * 320)

    decoded = codec.decode(b"\x55" * 160)

    assert len(decoded) == PublicAudioFormat(11025, 2, 16).frame_size


def test_contending_socket_context_preserves_original_timeout():
    attempted_lock = Event()
    finish_worker = Event()
    worker_entered = Event()
    observed_timeouts = []
    timeouts_at_unlock = []
    errors = []

    class ObservedLock:
        def __init__(self):
            self.lock = Lock()

        def __enter__(self):
            attempted_lock.set()
            self.lock.acquire()

        def __exit__(self, *args):
            timeouts_at_unlock.append(sock.gettimeout())
            self.lock.release()

    lock = ObservedLock()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2.5)

        def worker():
            try:
                with acquired_lock_and_unblocked_socket(lock, sock):
                    worker_entered.set()
                    if not finish_worker.wait(5):
                        raise RuntimeError("Timed out waiting for first owner")
                    observed_timeouts.append(sock.gettimeout())
            except Exception as exc:
                errors.append(exc)

        thread = Thread(target=worker, daemon=True)
        try:
            with acquired_lock_and_unblocked_socket(lock, sock):
                attempted_lock.clear()
                thread.start()
                assert attempted_lock.wait(5)
            assert worker_entered.wait(5)
        finally:
            finish_worker.set()
            thread.join(5)

        assert not thread.is_alive()
        assert not errors
        assert observed_timeouts == [0.0]
        assert timeouts_at_unlock == [2.5, 2.5]
        assert sock.gettimeout() == 2.5
