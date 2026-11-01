from types import SimpleNamespace

import pytest

from rfcvoip import Telemetry


def message_with_mapping(payload_type, rtpmap):
    return SimpleNamespace(
        body={
            "m": [
                {
                    "type": "audio",
                    "port": 4000,
                    "protocol": "RTP/AVP",
                    "methods": [str(payload_type)],
                    "attributes": {str(payload_type): {"rtpmap": rtpmap}},
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "rtpmap",
    [
        {"name": "unknown-codec", "frequency": 8000},
        {"name": "PCMU", "frequency": 16000},
        {"name": "PCMU", "frequency": 8000, "encoding": 2},
    ],
)
def test_explicit_unsupported_rtpmap_does_not_use_static_payload(rtpmap):
    message = message_with_mapping(0, rtpmap)

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is False
    assert report["compatible"] == []
    assert report["remote"][0]["supported"] is False
    assert report["remote"][0]["name"] == rtpmap["name"]


def test_explicit_supported_rtpmap_can_remap_static_payload():
    message = message_with_mapping(9, {"name": "PCMU", "frequency": 8000})

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is True
    assert report["compatible"][0]["name"] == "PCMU"
    assert report["compatible"][0]["payload_type"] == 9


def test_missing_rtpmap_uses_static_payload():
    message = message_with_mapping(0, {})

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is True
    assert report["compatible"][0]["name"] == "PCMU"


@pytest.mark.parametrize("payload_type", [-1, 128, "invalid"])
def test_invalid_rtp_payload_number_cannot_report_call_compatibility(
    payload_type,
):
    message = message_with_mapping(
        payload_type, {"name": "PCMU", "frequency": 8000}
    )

    report = Telemetry.codec_support_report(message)

    assert report["can_start_call"] is False
    assert report["compatible"] == []
    assert report["remote"][0]["supported"] is False
    assert "payload" in report["remote"][0]["availability_reason"].lower()
