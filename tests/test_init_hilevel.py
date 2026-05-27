import importlib

import pytest

import rfcvoip
from rfcvoip.RTP import PayloadType
from rfcvoip._version import __version__


def test_package_exports_version_metadata() -> None:
    assert rfcvoip.__version__ == __version__
    assert rfcvoip.version_info == tuple(
        int(part) if part.isdigit() else part
        for part in __version__.split("+", 1)[0].split(".")
    )


def test_supported_codecs_are_available_on_import() -> None:
    assert PayloadType.PCMU in rfcvoip.RTPCompatibleCodecs
    assert PayloadType.PCMA in rfcvoip.RTPCompatibleCodecs
    assert PayloadType.EVENT in rfcvoip.RTPCompatibleCodecs


def test_refresh_supported_codecs_updates_package_global(monkeypatch) -> None:
    monkeypatch.setattr(rfcvoip, "RTPCompatibleCodecs", [])

    refreshed = rfcvoip.refresh_supported_codecs()

    assert refreshed == list(rfcvoip.RTPCompatibleCodecs)
    assert PayloadType.PCMU in refreshed
    assert PayloadType.EVENT in refreshed


def test_codec_priority_helpers_update_and_reset_public_codec_order() -> None:
    try:
        rfcvoip.reset_codec_priorities()
        high_priority = rfcvoip.codec_priority_score(PayloadType.PCMU) + 10_000

        updated_codecs = rfcvoip.set_codec_priority(
            PayloadType.PCMA,
            high_priority,
        )

        assert updated_codecs == list(rfcvoip.RTPCompatibleCodecs)
        assert rfcvoip.codec_priority_score(PayloadType.PCMA) == high_priority
        assert rfcvoip.codec_priorities()["PCMA"] == high_priority

        pcmu_pcma_order = [
            codec
            for codec in rfcvoip.RTPCompatibleCodecs
            if codec in (PayloadType.PCMU, PayloadType.PCMA)
        ]
        assert pcmu_pcma_order[0] == PayloadType.PCMA

        reset_codecs = rfcvoip.reset_codec_priorities()

        assert reset_codecs == list(rfcvoip.RTPCompatibleCodecs)
        assert rfcvoip.codec_priority_score(PayloadType.PCMA) != high_priority
    finally:
        rfcvoip.reset_codec_priorities()


def test_telemetry_is_loaded_through_package_getattr() -> None:
    rfcvoip.__dict__.pop("Telemetry", None)

    telemetry = rfcvoip.Telemetry

    assert telemetry is importlib.import_module("rfcvoip.Telemetry")


def test_unknown_lazy_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        getattr(rfcvoip, "NotARealPublicAttribute")


def test_debug_output_respects_debug_flag(capsys, monkeypatch) -> None:
    monkeypatch.setattr(rfcvoip, "DEBUG", False)

    rfcvoip.debug("full debug output")
    assert capsys.readouterr().out == ""

    rfcvoip.debug("full debug output", "fallback error output")
    quiet_output = capsys.readouterr().out
    assert "fallback error output" in quiet_output
    assert "full debug output" not in quiet_output

    monkeypatch.setattr(rfcvoip, "DEBUG", True)

    rfcvoip.debug("full debug output", "fallback error output")
    debug_output = capsys.readouterr().out
    assert "[rfcvoip " in debug_output
    assert "full debug output" in debug_output
    assert "fallback error output" not in debug_output