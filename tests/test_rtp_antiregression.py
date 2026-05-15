from rfcvoip.RTP import RTPClient, PayloadType, TransmitType


def test_rtp_client_stop_before_start_is_safe():
    client = RTPClient(
        {0: PayloadType.PCMU},
        "127.0.0.1",
        10000,
        "127.0.0.1",
        10002,
        TransmitType.SENDRECV,
    )

    client.stop()
