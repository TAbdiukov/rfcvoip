from pathlib import Path

import rfcvoip


def test_version_info_is_derived_from___version__():
    expected = tuple(
        int(part) if part.isdigit() else part
        for part in rfcvoip.__version__.split("+", 1)[0].split(".")
    )
    assert rfcvoip.version_info == expected


def test_version_has_a_single_code_source():
    repo_root = Path(__file__).resolve().parents[1]
    version_assignment = f'__version__ = "{rfcvoip.__version__}"'
    version_file = repo_root / "rfcvoip" / "_version.py"

    assert version_assignment in version_file.read_text(encoding="utf-8")

    consumer_files = [
        repo_root / "rfcvoip" / "__init__.py",
        repo_root / "setup.py",
        repo_root / "docs" / "conf.py",
    ]
    for path in consumer_files:
        assert rfcvoip.__version__ not in path.read_text(encoding="utf-8")
