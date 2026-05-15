"""Package-level smoke tests for CI.

These tests keep pytest useful even before the project has broader behavioural
coverage.  They verify that every Python file in the package compiles and that
the package's importable modules can be imported without immediate runtime
errors.
"""

from __future__ import annotations

import compileall
import importlib
import pkgutil
from pathlib import Path

import pytest


PACKAGE_NAME = "rfcvoip"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / PACKAGE_NAME


def test_whole_package_compiles() -> None:
    assert compileall.compile_dir(
        str(PACKAGE_ROOT),
        quiet=1,
        force=True,
    )


def _package_module_names() -> list[str]:
    package = importlib.import_module(PACKAGE_NAME)
    module_names = {PACKAGE_NAME}

    for module in pkgutil.walk_packages(
        package.__path__,
        prefix=f"{PACKAGE_NAME}.",
    ):
        module_names.add(module.name)

    return sorted(module_names)


@pytest.mark.parametrize("module_name", _package_module_names())
def test_package_modules_import(module_name: str) -> None:
    importlib.import_module(module_name)
