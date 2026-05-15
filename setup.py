import ast
from pathlib import Path

from setuptools import find_packages
from setuptools import setup


ROOT = Path(__file__).resolve().parent


def read_version() -> str:
    version_path = ROOT / "rfcvoip" / "_version.py"
    module = ast.parse(
        version_path.read_text(encoding="utf-8"),
        filename=str(version_path),
    )
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    return node.value.value
                raise RuntimeError("__version__ must be a string literal.")
    raise RuntimeError("__version__ not found.")


with open(ROOT / "README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="rfcvoip",
    version=read_version(),
    description="rfcvoip is a maintained, protocol-focused VoIP/SIP/RTP library.",
    install_requires=[
        'audioop-lts>=0.2.1; python_version>="3.13"',
        "dnspython>=2.2.1",
    ],
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Tim Abdiukov; Tayler Porter and PyVoIP contributors",
    url="https://github.com/TAbdiukov/rfcvoip",
    project_urls={
        "Bug Tracker": "https://github.com/TAbdiukov/rfcvoip/issues",
        "Documentation": "https://rfcvoip.readthedocs.io/",
        "Original PyVoIP": "https://github.com/tayler6000/pyVoIP",
    },
    extras_require={
        "opus": ["discord.py>=2.0"],
        "silk": ["silk-python>=0.2.6"],
        "all": ["discord.py>=2.0", "silk-python>=0.2.6"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Telecommunications Industry",
        "Intended Audience :: Information Technology",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Natural Language :: English",
        "Topic :: Communications :: Internet Phone",
        "Topic :: Communications :: Telephony",
    ],
    packages=find_packages(exclude=("tests", "tests.*")),
    package_data={"rfcvoip": ["py.typed"]},
    python_requires=">=3.8",
)
