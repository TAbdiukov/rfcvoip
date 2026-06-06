from rfcvoip import Telemetry


def _codec(
    name,
    label,
    *,
    available=True,
    extra_packages=None,
    requires_extra_package=False,
    requires_compiler=False,
):
    return {
        "name": name,
        "available": available,
        "requires_extra_package": requires_extra_package,
        "requires_compiler": requires_compiler,
        "extra_packages": list(extra_packages or []),
        "rtp_options": [{"label": label}],
    }


def sample_codec_report() -> str:
    return Telemetry.report(
        {
            "codecs": {
                "known_codecs": [
                    _codec("PCMU-WB", "PCMU-WB/16000"),
                    _codec("PCMA", "PCMA/8000"),
                    _codec("PCMU", "PCMU/8000"),
                    _codec("PCMA-WB", "PCMA-WB/16000"),
                    _codec(
                        "opus",
                        "opus/48000/2",
                        available=False,
                        extra_packages=["discord.py"],
                        requires_extra_package=True,
                    ),
                    _codec(
                        "SILK",
                        "SILK/24000",
                        available=False,
                        extra_packages=["silk-python"],
                        requires_extra_package=True,
                    ),
                    _codec(
                        "G722",
                        "G722/8000",
                        available=False,
                        extra_packages=["G722"],
                        requires_extra_package=True,
                        requires_compiler=True,
                    ),
                ]
            }
        }
    )


def verify_telemetry_codec_report(report: str) -> None:
    assert "🎧 Local codecs:" in report
    assert "built-in / no compiler:" in report
    assert (
        "  ✅ G.711: "
        "`PCMA/8000`, `PCMU/8000`, `PCMA-WB/16000`, `PCMU-WB/16000`"
    ) in report
    assert "extra package(s) / no compiler:" in report
    assert "  ❌ Not available: opus [discord.py], SILK [silk-python]" in report
    assert "extra package(s) / requires compiler:" in report
    assert "  ❌ Not available: G.722 [G722]" in report


def test_telemetry_report_groups_codecs_by_availability_and_dependency() -> None:
    verify_telemetry_codec_report(sample_codec_report())