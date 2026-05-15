from rfcvoip.RTP import RTPPacketManager


def assert_lock_released(packet_manager):
    acquired = packet_manager.bufferLock.acquire(blocking=False)
    assert acquired
    packet_manager.bufferLock.release()


def test_write_releases_lock_after_normal_write():
    packet_manager = RTPPacketManager()

    packet_manager.write(1000, b"aa")

    assert packet_manager.offset == 1000
    assert packet_manager.read(2) == b"aa"
    assert_lock_released(packet_manager)


def test_write_releases_lock_after_non_reset_rebuild():
    packet_manager = RTPPacketManager()

    packet_manager.write(1000, b"bb")
    packet_manager.write(998, b"aa")

    assert packet_manager.offset == 998
    assert packet_manager.read(4) == b"aabb"
    assert_lock_released(packet_manager)


def test_write_reset_rebuild_discards_old_packets():
    packet_manager = RTPPacketManager()

    packet_manager.write(200000, b"bb")
    packet_manager.write(0, b"aa")

    assert packet_manager.offset == 0
    assert packet_manager.read(2) == b"aa"
    assert packet_manager.log == {0: b"aa"}
    assert_lock_released(packet_manager)
