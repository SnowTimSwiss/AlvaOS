#!/usr/bin/env python3
"""
AlvaOS update signatures.

Every AlvaOS release package ships with a detached Ed25519 signature
(``<package>.deb.sig``). The privileged helper refuses to install an AlvaOS
package unless that signature verifies against the public key installed at
``/opt/alvaos/keys/update-signing.pub`` (root-owned, part of the package).

What is signed is a short statement, not the raw file, so the format can grow
later without breaking old verifiers:

    alvaos-update-v1
    sha256=<hex digest of the .deb>

The signature file is the base64-encoded 64-byte Ed25519 signature.
The public key file is the base64-encoded 32-byte raw Ed25519 public key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Optional

PUBLIC_KEY_PATH = '/opt/alvaos/keys/update-signing.pub'
SIGNATURE_SUFFIX = '.sig'
STATEMENT_HEADER = b'alvaos-update-v1\n'


class SignatureError(Exception):
    pass


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def statement_for_digest(sha256_hex: str) -> bytes:
    return STATEMENT_HEADER + f'sha256={sha256_hex}\n'.encode('ascii')


def _load_public_key(key_b64: bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = base64.b64decode(key_b64.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError('Update signing key is not valid base64') from None
    if len(raw) != 32:
        raise SignatureError('Update signing key has the wrong length')
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_file(package_path: str, signature_b64: bytes, public_key_b64: bytes) -> str:
    """Verify a package against a signature. Returns the package sha256."""
    from cryptography.exceptions import InvalidSignature

    public_key = _load_public_key(public_key_b64)
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError('Signature is not valid base64') from None
    if len(signature) != 64:
        raise SignatureError('Signature has the wrong length')

    sha256_hex = file_sha256(package_path)
    try:
        public_key.verify(signature, statement_for_digest(sha256_hex))
    except InvalidSignature:
        raise SignatureError('Signature does not match this package') from None
    return sha256_hex


def verify_installed_key(package_path: str, signature_path: str,
                         public_key_path: str = PUBLIC_KEY_PATH) -> str:
    try:
        with open(public_key_path, 'rb') as f:
            public_key_b64 = f.read()
    except FileNotFoundError:
        raise SignatureError(
            'No update signing key is installed; refusing to install unsigned updates'
        ) from None
    try:
        with open(signature_path, 'rb') as f:
            signature_b64 = f.read(4096)
    except FileNotFoundError:
        raise SignatureError('The update has no signature file (.sig)') from None
    return verify_file(package_path, signature_b64, public_key_b64)


def sign_file(package_path: str, private_key_b64: bytes) -> bytes:
    """Used by the release tooling. Returns the base64 signature."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = base64.b64decode(private_key_b64.strip(), validate=True)
    if len(raw) != 32:
        raise SignatureError('Private key has the wrong length')
    key = Ed25519PrivateKey.from_private_bytes(raw)
    return base64.b64encode(key.sign(statement_for_digest(file_sha256(package_path))))


def generate_keypair() -> tuple:
    """Returns (private_b64, public_b64)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private_raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(private_raw), base64.b64encode(public_raw)


def signature_path_for(package_path: str) -> str:
    return package_path + SIGNATURE_SUFFIX


def read_deb_package_name(package_path: str) -> Optional[str]:
    """Package name from the control file, via dpkg-deb (no root needed)."""
    return read_deb_field(package_path, 'Package')


def read_deb_version(package_path: str) -> Optional[str]:
    """Package version from the control file, via dpkg-deb (no root needed)."""
    return read_deb_field(package_path, 'Version')


def read_deb_field(package_path: str, field: str) -> Optional[str]:
    """One control field of a .deb, via dpkg-deb (no root needed)."""
    import subprocess

    try:
        res = subprocess.run(
            ['/usr/bin/dpkg-deb', '-f', package_path, field],
            capture_output=True, text=True, timeout=30, env={'LC_ALL': 'C'},
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    return (res.stdout or '').strip() or None
