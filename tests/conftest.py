"""Shared test setup.

The scripts under tools/ are commands, not a package: they are run as
`python tools/<name>.py`, so they are deliberately not installed and not
importable by name. Putting the folder on sys.path here lets the tests import
them exactly as the interpreter does when the script is run, without turning
tools/ into a package that would then need an __init__.py and a place in the
wheel.
"""

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
