"""Tests for backend/password_utils.py (pure stdlib, no Flask required)."""

import hashlib

import pytest

import password_utils as pw


def test_hash_password_shape():
    data = pw.hash_password("correct horse battery staple")
    assert data["algo"] == pw.PBKDF2_ALGO
    assert data["iterations"] == pw.PBKDF2_ITERATIONS
    # salt is hex of SALT_BYTES bytes
    assert len(data["salt"]) == pw.SALT_BYTES * 2
    bytes.fromhex(data["salt"])  # must be valid hex
    bytes.fromhex(data["password_hash"])  # must be valid hex
    assert data["password_hash"]


def test_hash_password_is_salted_and_unique():
    a = pw.hash_password("same-password")
    b = pw.hash_password("same-password")
    assert a["salt"] != b["salt"]
    assert a["password_hash"] != b["password_hash"]


def test_verify_correct_password():
    data = pw.hash_password("s3cret!")
    is_valid, needs_rehash = pw.verify_password("s3cret!", data)
    assert is_valid is True
    assert needs_rehash is False


def test_verify_wrong_password():
    data = pw.hash_password("s3cret!")
    is_valid, needs_rehash = pw.verify_password("not-it", data)
    assert is_valid is False
    assert needs_rehash is False


def test_verify_rejects_non_string_and_bad_data():
    data = pw.hash_password("pw")
    assert pw.verify_password(None, data) == (False, False)
    assert pw.verify_password("pw", None) == (False, False)
    assert pw.verify_password("pw", {}) == (False, False)
    assert pw.verify_password("pw", {"salt": "ab"}) == (False, False)


def test_hash_password_rejects_non_string():
    with pytest.raises(TypeError):
        pw.hash_password(12345)


def _legacy_record(password, salt="0a1b2c3d"):
    """Reproduce the old single-SHA-256 format AlvaOS shipped before PBKDF2."""
    return {
        "salt": salt,
        "password_hash": hashlib.sha256((password + salt).encode("utf-8")).hexdigest(),
    }


def test_verify_legacy_sha256_valid_signals_rehash():
    record = _legacy_record("oldpass")
    is_valid, needs_rehash = pw.verify_password("oldpass", record)
    assert is_valid is True
    assert needs_rehash is True


def test_verify_legacy_sha256_wrong_password():
    record = _legacy_record("oldpass")
    is_valid, needs_rehash = pw.verify_password("wrong", record)
    assert is_valid is False
    assert needs_rehash is False


def test_pbkdf2_with_fewer_iterations_signals_rehash():
    weak = pw.hash_password("pw", iterations=1000)
    assert weak["iterations"] == 1000
    is_valid, needs_rehash = pw.verify_password("pw", weak)
    assert is_valid is True
    assert needs_rehash is True


def test_pbkdf2_corrupt_salt_fails_gracefully():
    data = pw.hash_password("pw")
    data["salt"] = "not-hex!!"
    is_valid, needs_rehash = pw.verify_password("pw", data)
    assert is_valid is False
    assert needs_rehash is False
