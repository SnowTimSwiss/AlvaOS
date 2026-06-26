#!/usr/bin/env python3
"""
AlvaOS Password Utilities

Pure-stdlib password hashing and verification. Deliberately free of any Flask
or system dependencies so it can be unit-tested in isolation.

Hashing uses PBKDF2-HMAC-SHA256 with a per-password random salt. Older installs
that still carry the legacy single-SHA-256 hash are verified for backward
compatibility and flagged for transparent re-hashing on next successful login.
"""

import hashlib
import hmac
import secrets

# Algorithm marker stored alongside the hash so the format is self-describing.
PBKDF2_ALGO = "pbkdf2_sha256"

# OWASP-recommended iteration count for PBKDF2-HMAC-SHA256 (2023 guidance).
# Tunable: stored per-hash so existing hashes keep verifying if this changes.
PBKDF2_ITERATIONS = 600_000

# Salt length in bytes (hex-encoded when stored).
SALT_BYTES = 16


def hash_password(password, iterations=PBKDF2_ITERATIONS):
    """Hash a plaintext password.

    Returns a dict ready to be merged into auth.json:
        {algo, iterations, salt, password_hash}
    """
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    salt = secrets.token_hex(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    )
    return {
        "algo": PBKDF2_ALGO,
        "iterations": iterations,
        "salt": salt,
        "password_hash": derived.hex(),
    }


def verify_password(password, auth_data):
    """Verify a plaintext password against stored auth data.

    Returns a tuple (is_valid, needs_rehash). ``needs_rehash`` is True when the
    password is correct but stored in an outdated format (legacy SHA-256, or a
    PBKDF2 hash with fewer iterations than the current default), signalling the
    caller to persist a fresh hash.
    """
    if not isinstance(password, str) or not isinstance(auth_data, dict):
        return False, False

    stored = auth_data.get("password_hash") or ""
    salt = auth_data.get("salt") or ""
    if not stored or not salt:
        return False, False

    algo = auth_data.get("algo", "sha256")

    if algo == PBKDF2_ALGO:
        try:
            iterations = int(auth_data.get("iterations", PBKDF2_ITERATIONS))
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt),
                iterations,
            )
        except (ValueError, TypeError):
            return False, False
        is_valid = hmac.compare_digest(derived.hex(), stored)
        needs_rehash = is_valid and iterations < PBKDF2_ITERATIONS
        return is_valid, needs_rehash

    # Legacy format: a single SHA-256 over (password + salt).
    legacy = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
    is_valid = hmac.compare_digest(legacy, stored)
    # A valid legacy password should be upgraded to PBKDF2.
    return is_valid, is_valid
