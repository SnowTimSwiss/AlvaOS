# Backend tests

Unit tests for the AlvaOS backend. The current suite focuses on **pure logic**
that runs anywhere (no Linux, root, btrfs, or Docker required):

- `test_password_utils.py` — PBKDF2 hashing, verification, and legacy-hash upgrade.
- `test_common.py` — size parsing/formatting and date/int helpers.
- `test_auth_manager.py` — session lifecycle and login rate limiting
  (auto-skipped if Flask is not installed).

## Running

From the repository root:

```bash
# minimal: only what the pure tests need
pip install pytest
pytest

# full: also runs the Flask-dependent auth_manager tests
pip install -r backend/requirements-dev.txt
pytest
```

## Not covered yet (see ROADMAP.md)

System-touching managers (storage / docker / backup) still need smoke tests
behind a Linux/root marker, plus a real end-to-end run on hardware.
