"""Check that the package is correctly built and installed.

This test looks trivial, but it is the one that validates the src layout: the
package lives under src/, so it is importable only if it was actually installed
(`pip install -e .`). If it fails, the problem is in packaging — not in the
business code.
"""

import immo_gov


def test_package_is_importable() -> None:
    assert immo_gov.__name__ == "immo_gov"


def test_package_exposes_a_version() -> None:
    assert immo_gov.__version__
