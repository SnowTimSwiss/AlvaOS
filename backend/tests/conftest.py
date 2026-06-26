"""Pytest bootstrap: make the backend package importable from the tests.

Adds the ``backend/`` directory to ``sys.path`` so tests can simply
``import password_utils`` / ``import common`` regardless of the working
directory pytest is invoked from.
"""

import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
