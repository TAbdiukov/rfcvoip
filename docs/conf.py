import ast
from pathlib import Path

import sphinx_rtd_theme

# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

ROOT = Path(__file__).resolve().parents[1]


def read_version() -> str:
    version_path = ROOT / "pyVoIP" / "_version.py"
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


# -- Project information -----------------------------------------------------

project = 'pyVoIP'
copyright = '2025, Tayler Porter'
author = 'Tayler J Porter'

# The short X.Y version and the full version, including alpha/beta/rc tags.
release = read_version()
version = release.split("+", 1)[0]

master_doc = 'index'

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx_rtd_theme"
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme = 'sphinx_rtd_theme'

#pygments_style = 'sphinx'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']
