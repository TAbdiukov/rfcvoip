from types import SimpleNamespace

import pytest

from rfcvoip import Telemetry


def media_message(port):
    media = {
        "type": "audio",
        "protocol": "RTP/AVP",
        "methods": ["0"],
        "attributes": {},
    }
    if port is not None:
        media["port"] = port
    return SimpleNamespace(body={"m": [media]})


@pytest.mark.parametrize("port", [0, -1, 65536, "invalid"])
def test_disabled_or_invalid_media_port_cannot_report_call_compatibility(port):
    report = Telemetry.codec_support_report(media_message(port))

    assert report["can_start_call"] is False
    assert report["compatible"] == []
    assert report["transmittable_audio"] == []
    assert report["remote"][0]["codec_supported"] is True
    assert report["remote"][0]["supported"] is False
    assert "unsupported" in report["remote"][0]["tags"]


@pytest.mark.parametrize("port", [None, 4000, "4000", 65535])
def test_enabled_or_unspecified_media_port_retains_codec_support(port):
    report = Telemetry.codec_support_report(media_message(port))

    assert report["can_start_call"] is True
    assert len(report["compatible"]) == 1


def test_disabled_media_does_not_hide_another_usable_audio_stream():
    message = media_message(0)
    message.body["m"].extend(media_message(4000).body["m"])

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is True
    assert len(report["compatible"]) == 1
    assert len(report["unsupported"]) == 1
