import struct

import pytest

from rfcvoip import RTP


@pytest.mark.parametrize("packet_ms", [1, 2.5, 5, 10, 50])
def test_dtmf_update_clock_matches_requested_packet_interval(
    monkeypatch, packet_ms
):
    client = RTP.RTPClient(
        {0: RTP.PayloadType.PCMU, 101: RTP.PayloadType.EVENT},
        "127.0.0.1",
        0,
        "127.0.0.1",
        40002,
        RTP.TransmitType.SENDRECV,
    )
    now = [0.0]
    sent = []

    def sleep(delay):
        now[0] += delay

    def send(_payload_type, payload, **_kwargs):
        sent.append((now[0], payload))

    client.NSD = True
    monkeypatch.setattr(RTP.time, "sleep", sleep)
    monkeypatch.setattr(client, "_send_rtp_packet", send)

    assert client.transmit_dtmf("5", duration_ms=100, packet_ms=packet_ms)

    assert [b[0] - a[0] for a, b in zip(sent, sent[1:])] == pytest.approx(
        [packet_ms / 1000] * (len(sent) - 1)
    )
    final = [item for item in sent if item[1][1] & 0x80]
    assert len(final) == 3
    assert all(struct.unpack("!H", item[1][2:])[0] == 800 for item in final)
    assert final[0][0] == pytest.approx((100 - packet_ms) / 1000)
