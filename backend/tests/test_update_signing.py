"""Tests for update package signatures (backend/update_signing.py)."""

import pytest

import update_signing as u


@pytest.fixture
def signed_package(tmp_path):
    private_b64, public_b64 = u.generate_keypair()
    package = tmp_path / "alvaos-system_1.0.0_amd64.deb"
    package.write_bytes(b"pretend this is a .deb" * 1000)
    signature = u.sign_file(str(package), private_b64)
    sig_path = tmp_path / (package.name + ".sig")
    sig_path.write_bytes(signature)
    key_path = tmp_path / "update-signing.pub"
    key_path.write_bytes(public_b64)
    return package, sig_path, key_path


def test_valid_signature_verifies(signed_package):
    package, sig_path, key_path = signed_package
    digest = u.verify_installed_key(str(package), str(sig_path), str(key_path))
    assert digest == u.file_sha256(str(package))


def test_modified_package_is_rejected(signed_package):
    package, sig_path, key_path = signed_package
    package.write_bytes(package.read_bytes() + b"backdoor")
    with pytest.raises(u.SignatureError):
        u.verify_installed_key(str(package), str(sig_path), str(key_path))


def test_signature_from_another_key_is_rejected(signed_package, tmp_path):
    package, sig_path, _ = signed_package
    _, other_public = u.generate_keypair()
    other_key = tmp_path / "other.pub"
    other_key.write_bytes(other_public)
    with pytest.raises(u.SignatureError):
        u.verify_installed_key(str(package), str(sig_path), str(other_key))


def test_missing_key_or_signature_fails_closed(signed_package, tmp_path):
    package, sig_path, key_path = signed_package
    with pytest.raises(u.SignatureError):
        u.verify_installed_key(str(package), str(sig_path), str(tmp_path / "missing.pub"))
    with pytest.raises(u.SignatureError):
        u.verify_installed_key(str(package), str(tmp_path / "missing.sig"), str(key_path))


def test_garbage_signature_is_rejected(signed_package):
    package, sig_path, key_path = signed_package
    sig_path.write_bytes(b"not base64 !!!")
    with pytest.raises(u.SignatureError):
        u.verify_installed_key(str(package), str(sig_path), str(key_path))


def test_the_way_back_is_a_signed_package_of_the_installed_version(tmp_path):
    import os
    import time
    versions = {}

    def make(name, version, signed=True, age=0):
        path = tmp_path / name
        path.write_bytes(b"deb")
        os.utime(path, (time.time() - age, time.time() - age))
        if signed:
            (tmp_path / (name + ".sig")).write_text("sig")
        versions[str(path)] = version
        return str(path)

    old = make("alvaos_1.0.0_all.deb", "1.0.0", age=100)
    older_copy = make("copy-of-1.0.0.deb", "1.0.0", age=500)
    make("unsigned_1.0.0.deb", "1.0.0", signed=False)
    new = make("alvaos_2.0.0_all.deb", "2.0.0")
    found = u.packages_of_version(str(tmp_path), "1.0.0", exclude=new,
                                               read_version=versions.get)
    assert found == [old, older_copy]
    assert u.packages_of_version(str(tmp_path), "2.0.0", exclude=new, read_version=versions.get) == []
    assert u.packages_of_version(str(tmp_path), "", read_version=versions.get) == []
    assert u.packages_of_version(str(tmp_path / "missing"), "1.0.0") == []
