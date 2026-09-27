"""Tests for Buddy Backup stream encryption (backend/buddy_crypto.py)."""

import hashlib
import hmac
import os

import pytest

import buddy_crypto as bc

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def material():
    # Deriving is deliberately slow; share one key across the module.
    return bc.new_key_material(PASSWORD)


def write(path, data):
    path.write_bytes(data)
    return str(path)


def key_from_password(password):
    return lambda h: bc.derive_master_key(password, h.kdf_salt, h.log2_n, h.r, h.p)


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 5 * 1024 + 7])
def test_round_trip_with_the_password_only(tmp_path, material, size):
    plain = os.urandom(size)
    src = write(tmp_path / "plain", plain)
    enc = str(tmp_path / "plain.enc")
    out = str(tmp_path / "restored")
    bc.encrypt_file(src, enc, material, chunk_size=1024)
    # Restore needs nothing but the password: salt and parameters are in the header.
    bc.decrypt_file(enc, out, key_from_password(PASSWORD))
    assert open(out, "rb").read() == plain


def test_ciphertext_does_not_contain_plaintext(tmp_path, material):
    plain = b"very secret family photo " * 200
    src = write(tmp_path / "plain", plain)
    enc = str(tmp_path / "plain.enc")
    bc.encrypt_file(src, enc, material, chunk_size=1024)
    assert b"secret family" not in open(enc, "rb").read()


def test_two_encryptions_differ(tmp_path, material):
    src = write(tmp_path / "plain", b"same input" * 100)
    bc.encrypt_file(src, str(tmp_path / "a"), material)
    bc.encrypt_file(src, str(tmp_path / "b"), material)
    assert (tmp_path / "a").read_bytes() != (tmp_path / "b").read_bytes()


def test_wrong_password_fails_and_leaves_no_output(tmp_path, material):
    src = write(tmp_path / "plain", b"x" * 5000)
    enc = str(tmp_path / "e")
    out = tmp_path / "out"
    bc.encrypt_file(src, enc, material, chunk_size=1024)
    with pytest.raises(bc.BuddyCryptoError, match="Wrong encryption password"):
        bc.decrypt_file(enc, str(out), key_from_password("wrong password"))
    assert not out.exists()


def _encrypted(tmp_path, material, size=5 * 1024):
    src = write(tmp_path / "plain", os.urandom(size))
    enc = tmp_path / "e"
    bc.encrypt_file(src, str(enc), material, chunk_size=1024)
    return enc


def test_bit_flip_is_detected(tmp_path, material):
    enc = _encrypted(tmp_path, material)
    data = bytearray(enc.read_bytes())
    data[bc.HEADER_LEN + 2000] ^= 1
    enc.write_bytes(bytes(data))
    with pytest.raises(bc.BuddyCryptoError):
        bc.decrypt_file(str(enc), str(tmp_path / "o"), lambda h: material.master_key)


def test_header_tampering_is_detected(tmp_path, material):
    enc = _encrypted(tmp_path, material)
    data = bytearray(enc.read_bytes())
    data[60] ^= 1  # inside the nonce prefix
    enc.write_bytes(bytes(data))
    with pytest.raises(bc.BuddyCryptoError):
        bc.decrypt_file(str(enc), str(tmp_path / "o"), lambda h: material.master_key)


def test_truncation_at_a_chunk_boundary_is_detected(tmp_path, material):
    enc = _encrypted(tmp_path, material)
    sealed = 1024 + bc.TAG_LEN
    data = enc.read_bytes()
    enc.write_bytes(data[: bc.HEADER_LEN + 3 * sealed])  # drop the tail cleanly
    with pytest.raises(bc.BuddyCryptoError):
        bc.decrypt_file(str(enc), str(tmp_path / "o"), lambda h: material.master_key)


def test_reordered_chunks_are_detected(tmp_path, material):
    enc = _encrypted(tmp_path, material)
    sealed = 1024 + bc.TAG_LEN
    data = enc.read_bytes()
    h, c0, c1, rest = (data[: bc.HEADER_LEN], data[bc.HEADER_LEN:bc.HEADER_LEN + sealed],
                       data[bc.HEADER_LEN + sealed:bc.HEADER_LEN + 2 * sealed], data[bc.HEADER_LEN + 2 * sealed:])
    enc.write_bytes(h + c1 + c0 + rest)
    with pytest.raises(bc.BuddyCryptoError):
        bc.decrypt_file(str(enc), str(tmp_path / "o"), lambda h: material.master_key)


def test_key_material_json_round_trip(material):
    restored = bc.KeyMaterial.from_json(material.to_json())
    assert restored.master_key == material.master_key
    assert restored.kdf_salt == material.kdf_salt


# ── Legacy ALVAENC1 compatibility ────────────────────────────────────────────

def _original_v1_encrypt(plain: bytes, key: bytes, nonce: bytes) -> bytes:
    """The pre-ALVAENC2 implementation, verbatim in behaviour."""
    out = bytearray()
    counter = 0
    for start in range(0, len(plain), 1024 * 1024):
        data = plain[start:start + 1024 * 1024]
        cursor = 0
        while cursor < len(data):
            block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
            take = min(len(block), len(data) - cursor)
            out.extend(bytes(data[cursor + i] ^ block[i] for i in range(take)))
            cursor += take
            counter += 1
    header = b"ALVAENC1" + nonce
    mac = hmac.new(hashlib.sha256(key + b":mac").digest(), digestmod=hashlib.sha256)
    mac.update(header)
    mac.update(bytes(out))
    return header + bytes(out) + mac.digest()


def test_old_streams_still_decrypt(tmp_path):
    key = bc.legacy_v1_key("hash", "salt")
    plain = os.urandom(3000)
    enc = tmp_path / "old.enc"
    enc.write_bytes(_original_v1_encrypt(plain, key, os.urandom(16)))
    out = tmp_path / "out"
    bc.decrypt_legacy_v1_file(str(enc), str(out), key)
    assert out.read_bytes() == plain


def test_legacy_encrypt_matches_the_original_format(tmp_path):
    key = bc.legacy_v1_key("hash", "salt")
    plain = os.urandom(2500)
    src = write(tmp_path / "p", plain)
    enc = tmp_path / "e"
    bc.encrypt_legacy_v1_file(src, str(enc), key)
    nonce = enc.read_bytes()[8:24]
    assert enc.read_bytes() == _original_v1_encrypt(plain, key, nonce)


def test_legacy_wrong_key_writes_nothing(tmp_path):
    key = bc.legacy_v1_key("hash", "salt")
    enc = tmp_path / "e"
    enc.write_bytes(_original_v1_encrypt(b"data" * 100, key, os.urandom(16)))
    out = tmp_path / "out"
    with pytest.raises(bc.BuddyCryptoError):
        bc.decrypt_legacy_v1_file(str(enc), str(out), bc.legacy_v1_key("other", "salt"))
    assert not out.exists()


# ── Manager integration: the disaster-recovery case ──────────────────────────

@pytest.fixture
def manager_factory(tmp_path, monkeypatch):
    import buddy_backup_manager as bbm

    def make(name):
        state = tmp_path / name
        state.mkdir()
        monkeypatch.setattr(bbm.BuddyBackupManager, "_resolve_state_dir", lambda self: str(state))
        return bbm.BuddyBackupManager(lambda *a, **k: (None, "no commands in tests"))
    return make


def encrypt_as(nas, src, enc):
    """Encrypt the way a NAS does: ALVAENC2 with its key material, else the old format."""
    material = nas._encryption_material()
    if material is not None:
        bc.encrypt_file(src, enc, material)
        return
    settings = nas.get_settings(include_secret=True)
    key = bc.legacy_v1_key(settings["encryption_hash"], settings["encryption_salt"])
    bc.encrypt_legacy_v1_file(src, enc, key)


def test_fresh_machine_restores_with_only_the_password(tmp_path, manager_factory):
    old_nas = manager_factory("old")
    ok, _ = old_nas.save_settings({"encryption_enabled": True, "encryption_password": PASSWORD})
    assert ok
    plain = os.urandom(10_000)
    src = write(tmp_path / "snapshot.stream", plain)
    enc = str(tmp_path / "snapshot.enc")
    encrypt_as(old_nas, src, enc)
    assert bc.is_encrypted_stream(enc) == "v2"

    # The old NAS is gone. A new install has no settings and no keys.
    new_nas = manager_factory("new")
    out = str(tmp_path / "restored")
    ok, err = new_nas._decrypt_stream_with_passphrase(enc, out, PASSWORD)
    assert ok, err
    assert open(out, "rb").read() == plain

    ok, err = new_nas._decrypt_stream_with_passphrase(enc, str(tmp_path / "x"), "wrong password")
    assert not ok and "Wrong encryption password" in err


def test_pre_upgrade_install_keeps_working_and_upgrades(tmp_path, manager_factory):
    nas = manager_factory("legacy")
    ok, _ = nas.save_settings({"encryption_enabled": True, "encryption_password": PASSWORD})
    assert ok
    # Simulate an install from before ALVAENC2: settings exist, key file does not.
    os.remove(nas.encryption_key_file)
    src = write(tmp_path / "s", b"legacy data" * 50)
    enc_old = str(tmp_path / "old.enc")
    encrypt_as(nas, src, enc_old)
    assert bc.is_encrypted_stream(enc_old) == "v1"

    # Old-format snapshots still restore with the password on the same machine.
    ok, err = nas._decrypt_stream_with_passphrase(enc_old, str(tmp_path / "o1"), PASSWORD)
    assert ok, err

    # Entering the password once creates ALVAENC2 key material.
    assert nas._encryption_material() is None
    assert nas.verify_encryption_passphrase(PASSWORD)
    enc_new = str(tmp_path / "new.enc")
    encrypt_as(nas, src, enc_new)
    assert bc.is_encrypted_stream(enc_new) == "v2"


def test_password_change_keeps_old_snapshots_restorable(tmp_path, manager_factory):
    nas = manager_factory("change")
    nas.save_settings({"encryption_enabled": True, "encryption_password": PASSWORD})
    src = write(tmp_path / "s", b"before the change" * 20)
    enc = str(tmp_path / "before.enc")
    encrypt_as(nas, src, enc)
    nas.save_settings({"encryption_enabled": True, "encryption_password": "a brand new password"})
    ok, err = nas._decrypt_stream_with_passphrase(enc, str(tmp_path / "o"), PASSWORD)
    assert ok, err


def test_key_file_is_private(manager_factory):
    nas = manager_factory("perm")
    nas.save_settings({"encryption_enabled": True, "encryption_password": PASSWORD})
    assert (os.stat(nas.encryption_key_file).st_mode & 0o077) == 0


def test_short_passwords_are_rejected(manager_factory):
    nas = manager_factory("short")
    ok, payload = nas.save_settings({"encryption_enabled": True, "encryption_password": "1234"})
    assert not ok and "8 characters" in payload["error"]


def test_vault_key_opens_with_material_or_password(material):
    key = os.urandom(32)
    blob = bc.seal_vault_key(key, material)
    assert bc.open_vault_key(blob, material=material) == key
    assert bc.open_vault_key(blob, passphrase=PASSWORD) == key      # a replacement NAS
    assert bc.vault_key_salt(blob) == material.kdf_salt
    with pytest.raises(bc.BuddyCryptoError, match="Wrong"):
        bc.open_vault_key(blob, passphrase="not the password")
    with pytest.raises(bc.BuddyCryptoError, match="password is needed"):
        bc.open_vault_key(blob)
    tampered = blob[:-2] + ("A" if blob[-2] != "A" else "B") + blob[-1]
    with pytest.raises(bc.BuddyCryptoError):
        bc.open_vault_key(tampered, material=material)
