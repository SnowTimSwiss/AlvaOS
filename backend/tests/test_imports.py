"""Every backend module imports on its own.

A merge once left files_server.py using a name it no longer imported; the
unit tests of other modules passed, and the Files server and the Hub did not
start on the NAS. Each module is imported in a fresh interpreter, so one that
only works after another has been imported shows up too.
"""

import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MODULES = sorted(p.stem for p in BACKEND.glob("*.py") if p.stem.isidentifier())
# Run as programs, not imported: alvaos-backend.py starts the admin server.
SCRIPTS = ["alvaos-priv"]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    res = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=BACKEND,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[-2000:]


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_compiles(name):
    compile((BACKEND / name).read_text(encoding="utf-8"), name, "exec")
