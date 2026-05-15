import py_compile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "rfcvoip"


def test_whole_package_compiles(tmp_path):
    failures = []

    for source in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative_source = source.relative_to(PACKAGE_ROOT)
        compiled_target = tmp_path / relative_source.with_suffix(".pyc")
        compiled_target.parent.mkdir(parents=True, exist_ok=True)

        try:
            py_compile.compile(
                str(source),
                cfile=str(compiled_target),
                doraise=True,
            )
        except py_compile.PyCompileError as exc:
            failures.append(f"{relative_source}: {exc}")

    assert not failures, "\n".join(failures)