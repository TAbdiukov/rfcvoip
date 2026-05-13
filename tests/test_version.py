from pathlib import Path

import pyVoIP


def test_version_info_is_derived_from___version__():
    expected = tuple(
        int(part) if part.isdigit() else part
        for part in pyVoIP.__version__.split("+", 1)[0].split(".")
    )
    assert pyVoIP.version_info == expected


def test_version_has_a_single_code_source():
    repo_root = Path(__file__).resolve().parents[1]
    version_assignment = f'__version__ = "{pyVoIP.__version__}"'
    version_file = repo_root / "pyVoIP" / "_version.py"

    assert version_assignment in version_file.read_text(encoding="utf-8")

    consumer_files = [
        repo_root / "pyVoIP" / "__init__.py",
        repo_root / "setup.py",
        repo_root / "docs" / "conf.py",
    ]
    for path in consumer_files:
        assert pyVoIP.__version__ not in path.read_text(encoding="utf-8")
