"""Every advertised optional-codec bundle must meet the same runtime minimum."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace


def test_no_compiler_and_all_install_same_supported_opus_dependency(monkeypatch):
    metadata = {}
    monkeypatch.setitem(sys.modules, "setuptools", SimpleNamespace(
        setup=lambda **kwargs: metadata.update(kwargs),
        find_packages=lambda **kwargs: [],
    ))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "setup.py"))
    extras = metadata["extras_require"]
    opus_requirement = next(req for req in extras["opus"] if req.startswith("discord.py"))
    assert opus_requirement == "discord.py>=2.7.1"
    for bundle in ("no-compiler", "all"):
        assert [req for req in extras[bundle] if req.startswith("discord.py")] == [opus_requirement]
