from types import SimpleNamespace

import pytest

import rfcvoip
from rfcvoip import RTP, Telemetry
from rfcvoip.codecs.base import CodecAvailability
from rfcvoip.codecs.opus import OpusCodec


@pytest.fixture
def unavailable_opus_with_stale_enabled_list(monkeypatch):
    monkeypatch.setattr(rfcvoip, "RTPCompatibleCodecs", [RTP.PayloadType.OPUS])
    monkeypatch.setattr(
        OpusCodec,
        "availability",
        classmethod(
            lambda cls: CodecAvailability(False, "backend unavailable")
        ),
    )


def test_explicit_support_hint_cannot_override_unavailable_codec(
    unavailable_opus_with_stale_enabled_list,
):
    info = Telemetry.codec_info(RTP.PayloadType.OPUS, supported=True)

    assert info["available"] is False
    assert info["supported"] is False
    assert info["codec_supported"] is False
    assert "unsupported" in info["tags"]


def test_remote_compatibility_does_not_include_unavailable_enabled_codec(
    unavailable_opus_with_stale_enabled_list,
):
    message = SimpleNamespace(
        body={
            "m": [
                {
                    "type": "audio",
                    "protocol": "RTP/AVP",
                    "methods": ["111"],
                    "attributes": {
                        "111": {
                            "rtpmap": {
                                "name": "opus",
                                "frequency": 48000,
                                "encoding": 2,
                            }
                        }
                    },
                }
            ]
        }
    )

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is False
    assert report["compatible"] == []
    assert report["remote"][0]["available"] is False
    assert report["remote"][0]["supported"] is False
    assert report["remote"][0]["codec_supported"] is False
