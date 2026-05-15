from rfcvoip.VoIP import VoIPPhone


def test_voip_phone_local_codec_offer_contains_audio_and_events():
    from rfcvoip import Telemetry

    phone = VoIPPhone(
        "127.0.0.1",
        5060,
        "alice",
        "secret",
        myIP="127.0.0.1",
        rtpPortLow=10000,
        rtpPortHigh=10010,
    )

    offer = Telemetry.local_codec_offer(phone)
    names = {codec["name"] for codec in offer}

    assert "PCMU" in names
    assert "PCMA" in names
    assert "telephone-event" in names
    assert any(codec["can_transmit_audio"] for codec in offer)