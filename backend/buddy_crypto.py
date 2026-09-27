#!/usr/bin/env python3
"""
Buddy Backup stream encryption (format ALVAENC2).

Goals:
- Standard, reviewed primitives only: scrypt for the passphrase, HKDF for
  per-stream keys, AES-256-GCM for the data (via the `cryptography` package).
- Disaster recovery must work: everything needed to derive the key except the
  passphrase is stored in the stream header, so a freshly installed AlvaOS
  can restore from a buddy with nothing but the passphrase.
- Streaming with bounded memory: the file is sealed in fixed-size chunks.
  Chunk order and the end of the stream are authenticated, so reordering,
  dropping or truncating chunks is detected.

Header (72 bytes, also the associated data of every chunk):

    magic         8   b"ALVAENC2"
    kdf           1   1 = scrypt
    log2_n        1   scrypt cost
    r             1
    p             1
    kdf_salt     16   per-passphrase salt (the same for all streams of a key)
    stream_salt  32   random per stream; HKDF salt for the stream key
    nonce_prefix  7   random per stream
    chunk_size    4   big-endian plaintext bytes per chunk

Chunks: AES-256-GCM(stream_key, nonce = nonce_prefix || counter(4) || last(1),
aad = header), each ciphertext is chunk_size + 16 bytes except the last one.
The last chunk has last = 1 and may be empty.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
from dataclasses import dataclass
from typing import Any, Callable, Optional

MAGIC_V2 = b"ALVAENC2"
MAGIC_V1 = b"ALVAENC1"
KDF_SCRYPT = 1
DEFAULT_LOG2_N = 16  # 64 MiB of memory, well under a second on NAS hardware
DEFAULT_R = 8
DEFAULT_P = 1
CHUNK_SIZE = 1024 * 1024
TAG_LEN = 16
HEADER_STRUCT = struct.Struct(">8sBBBB16s32s7sI")
HEADER_LEN = HEADER_STRUCT.size


class BuddyCryptoError(Exception):
    pass


@dataclass
class KeyMaterial:
    """What a machine stores to encrypt unattended: the scrypt output + params."""
    master_key: bytes
    kdf_salt: bytes
    log2_n: int = DEFAULT_LOG2_N
    r: int = DEFAULT_R
    p: int = DEFAULT_P

    def to_json(self) -> dict:
        return {
            "version": 2,
            "kdf": "scrypt",
            "log2_n": self.log2_n,
            "r": self.r,
            "p": self.p,
            "salt": base64.b64encode(self.kdf_salt).decode("ascii"),
            "key": base64.b64encode(self.master_key).decode("ascii"),
        }

    @classmethod
    def from_json(cls, data: dict) -> "KeyMaterial":
        try:
            return cls(
                master_key=base64.b64decode(data["key"]),
                kdf_salt=base64.b64decode(data["salt"]),
                log2_n=int(data.get("log2_n", DEFAULT_LOG2_N)),
                r=int(data.get("r", DEFAULT_R)),
                p=int(data.get("p", DEFAULT_P)),
            )
        except Exception as exc:
            raise BuddyCryptoError(f"Invalid stored encryption key: {exc}") from exc


def derive_master_key(passphrase: str, kdf_salt: bytes, log2_n: int = DEFAULT_LOG2_N,
                      r: int = DEFAULT_R, p: int = DEFAULT_P) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    if not passphrase:
        raise BuddyCryptoError("Encryption password is empty")
    if not (10 <= log2_n <= 22 and 1 <= r <= 32 and 1 <= p <= 16):
        raise BuddyCryptoError("Unsupported key derivation parameters")
    kdf = Scrypt(salt=kdf_salt, length=32, n=2 ** log2_n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8"))


def new_key_material(passphrase: str) -> KeyMaterial:
    salt = secrets.token_bytes(16)
    return KeyMaterial(master_key=derive_master_key(passphrase, salt), kdf_salt=salt)


def _stream_key(master_key: bytes, stream_salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=stream_salt, info=b"alvaos-buddy-stream-v2"
    ).derive(master_key)


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    if counter >= 2 ** 32:
        raise BuddyCryptoError("Stream is too large")
    return prefix + counter.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


@dataclass
class StreamHeader:
    raw: bytes
    log2_n: int
    r: int
    p: int
    kdf_salt: bytes
    stream_salt: bytes
    nonce_prefix: bytes
    chunk_size: int


def parse_header(raw: bytes) -> StreamHeader:
    if len(raw) < HEADER_LEN:
        raise BuddyCryptoError("Encrypted stream is too small")
    magic, kdf, log2_n, r, p, kdf_salt, stream_salt, nonce_prefix, chunk_size = \
        HEADER_STRUCT.unpack(raw[:HEADER_LEN])
    if magic != MAGIC_V2:
        raise BuddyCryptoError("Not an ALVAENC2 stream")
    if kdf != KDF_SCRYPT:
        raise BuddyCryptoError("Unsupported key derivation in stream header")
    if not 1024 <= chunk_size <= 64 * 1024 * 1024:
        raise BuddyCryptoError("Invalid chunk size in stream header")
    return StreamHeader(raw[:HEADER_LEN], log2_n, r, p, kdf_salt, stream_salt, nonce_prefix, chunk_size)


def read_magic(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read(8)


class StreamEncryptor:
    """Incremental ALVAENC2 encryption: feed plaintext, get ciphertext.

    Produces exactly the bytes encrypt_file() writes, without needing the
    whole plaintext on disk (btrfs send output is piped straight in).
    """

    def __init__(self, material: KeyMaterial, chunk_size: int = CHUNK_SIZE):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.chunk_size = chunk_size
        stream_salt = secrets.token_bytes(32)
        self._nonce_prefix = secrets.token_bytes(7)
        self.header = HEADER_STRUCT.pack(
            MAGIC_V2, KDF_SCRYPT, material.log2_n, material.r, material.p,
            material.kdf_salt, stream_salt, self._nonce_prefix, chunk_size,
        )
        self._aead = AESGCM(_stream_key(material.master_key, stream_salt))
        self._buffer = bytearray()
        self._counter = 0
        self._header_sent = False
        self._finished = False

    def _prefix(self) -> bytes:
        if self._header_sent:
            return b""
        self._header_sent = True
        return self.header

    def update(self, data: bytes) -> bytes:
        if self._finished:
            raise BuddyCryptoError("Encryptor already finished")
        self._buffer.extend(data)
        out = bytearray(self._prefix())
        # Keep at least one byte back: the final chunk must be sealed as "last".
        while len(self._buffer) > self.chunk_size:
            chunk = bytes(self._buffer[:self.chunk_size])
            del self._buffer[:self.chunk_size]
            out += self._aead.encrypt(_nonce(self._nonce_prefix, self._counter, False), chunk, self.header)
            self._counter += 1
        return bytes(out)

    def finalize(self) -> bytes:
        if self._finished:
            return b""
        self._finished = True
        out = self._prefix() + self._aead.encrypt(
            _nonce(self._nonce_prefix, self._counter, True), bytes(self._buffer), self.header
        )
        self._buffer.clear()
        return out


class StreamDecryptor:
    """Incremental ALVAENC2 decryption with the same guarantees as decrypt_file().

    Plaintext is only returned for chunks that authenticated. finalize()
    raises if the stream ended early, so a truncated download never counts
    as complete.
    """

    def __init__(self, master_key_for: Callable[[StreamHeader], bytes]):
        self._master_key_for = master_key_for
        self._buffer = bytearray()
        self._header: Optional[StreamHeader] = None
        self._aead: Any = None
        self._counter = 0
        self._finished = False

    def _decrypt(self, sealed: bytes, last: bool) -> bytes:
        from cryptography.exceptions import InvalidTag

        assert self._header is not None and self._aead is not None
        try:
            return self._aead.decrypt(_nonce(self._header.nonce_prefix, self._counter, last), sealed,
                                      self._header.raw)
        except InvalidTag:
            if self._counter == 0:
                raise BuddyCryptoError("Wrong encryption password, or the stream was modified") from None
            raise BuddyCryptoError("Encrypted stream was modified or truncated") from None

    def update(self, data: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._buffer.extend(data)
        if self._header is None:
            if len(self._buffer) < HEADER_LEN:
                return b""
            self._header = parse_header(bytes(self._buffer[:HEADER_LEN]))
            del self._buffer[:HEADER_LEN]
            self._aead = AESGCM(_stream_key(self._master_key_for(self._header), self._header.stream_salt))
        sealed_len = self._header.chunk_size + TAG_LEN
        out = bytearray()
        # A full sealed chunk followed by more data cannot be the last one.
        while len(self._buffer) > sealed_len:
            sealed = bytes(self._buffer[:sealed_len])
            del self._buffer[:sealed_len]
            out += self._decrypt(sealed, last=False)
            self._counter += 1
        return bytes(out)

    def finalize(self) -> bytes:
        if self._finished:
            return b""
        self._finished = True
        if self._header is None:
            raise BuddyCryptoError("Encrypted stream is too small")
        if len(self._buffer) < TAG_LEN:
            raise BuddyCryptoError("Encrypted stream is truncated")
        plain = self._decrypt(bytes(self._buffer), last=True)
        self._buffer.clear()
        return plain


def encrypt_file(plain_path: str, encrypted_path: str, material: KeyMaterial,
                 chunk_size: int = CHUNK_SIZE) -> None:
    encryptor = StreamEncryptor(material, chunk_size)
    with open(plain_path, "rb") as src, open(encrypted_path, "wb") as dst:
        for block in iter(lambda: src.read(chunk_size), b""):
            dst.write(encryptor.update(block))
        dst.write(encryptor.finalize())


def decrypt_file(encrypted_path: str, plain_path: str,
                 master_key_for: Callable[[StreamHeader], bytes]) -> None:
    """Decrypt a stream. `master_key_for(header)` returns the master key.

    That callback either returns a stored key (same machine) or derives it
    from the passphrase with the parameters in the header (fresh machine).
    Raises BuddyCryptoError on a wrong key or any tampering; the partial
    output file is removed in that case.
    """
    decryptor = StreamDecryptor(master_key_for)
    try:
        with open(encrypted_path, "rb") as src, open(plain_path, "wb") as dst:
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(decryptor.update(block))
            dst.write(decryptor.finalize())
    except Exception:
        try:
            os.remove(plain_path)
        except OSError:
            pass
        raise


# ── Legacy format (ALVAENC1) ─────────────────────────────────────────────────
# Kept only so snapshots made before ALVAENC2 can still be restored. The key
# was derived from the machine's stored passphrase hash, so these can only be
# decrypted with that machine's old settings.

def legacy_v1_key(encryption_hash: str, encryption_salt: str) -> bytes:
    return hashlib.sha256(f"{encryption_hash}|{encryption_salt}|alvaos-buddy-v1".encode("utf-8")).digest()


def _legacy_keystream_xor(data: bytes, key: bytes, nonce: bytes, counter: int):
    out = bytearray(len(data))
    cursor = 0
    while cursor < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        take = min(len(block), len(data) - cursor)
        # int.from_bytes XOR is ~50x faster than a per-byte generator.
        mixed = int.from_bytes(data[cursor:cursor + take], "big") ^ int.from_bytes(block[:take], "big")
        out[cursor:cursor + take] = mixed.to_bytes(take, "big")
        cursor += take
        counter += 1
    return bytes(out), counter


def decrypt_legacy_v1_file(encrypted_path: str, plain_path: str, key: bytes) -> None:
    header_len = len(MAGIC_V1) + 16
    tag_len = 32
    mac_key = hashlib.sha256(key + b":mac").digest()
    size = os.path.getsize(encrypted_path)
    if size < header_len + tag_len:
        raise BuddyCryptoError("Encrypted stream is too small")
    # Authenticate everything before writing any plaintext.
    with open(encrypted_path, "rb") as src:
        header = src.read(header_len)
        if not header.startswith(MAGIC_V1):
            raise BuddyCryptoError("Invalid encrypted stream header")
        mac = hmac.new(mac_key, digestmod=hashlib.sha256)
        mac.update(header)
        remaining = size - header_len - tag_len
        while remaining > 0:
            chunk = src.read(min(1024 * 1024, remaining))
            if not chunk:
                raise BuddyCryptoError("Encrypted stream is truncated")
            mac.update(chunk)
            remaining -= len(chunk)
        if not hmac.compare_digest(src.read(tag_len), mac.digest()):
            raise BuddyCryptoError("Wrong encryption password, or the stream was modified")
    nonce = header[len(MAGIC_V1):]
    try:
        with open(encrypted_path, "rb") as src, open(plain_path, "wb") as dst:
            src.seek(header_len)
            remaining = size - header_len - tag_len
            counter = 0
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                remaining -= len(chunk)
                plain, counter = _legacy_keystream_xor(chunk, key, nonce, counter)
                dst.write(plain)
    except Exception:
        try:
            os.remove(plain_path)
        except OSError:
            pass
        raise


def encrypt_legacy_v1_file(plain_path: str, encrypted_path: str, key: bytes) -> None:
    """Fallback for installs whose password predates ALVAENC2 key material.

    Used only until the user enters the encryption password once (which
    creates ALVAENC2 key material); see BuddyBackupManager._encryption_material.
    """
    nonce = secrets.token_bytes(16)
    header = MAGIC_V1 + nonce
    mac = hmac.new(hashlib.sha256(key + b":mac").digest(), digestmod=hashlib.sha256)
    mac.update(header)
    counter = 0
    with open(plain_path, "rb") as src, open(encrypted_path, "wb") as dst:
        dst.write(header)
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            cipher, counter = _legacy_keystream_xor(chunk, key, nonce, counter)
            dst.write(cipher)
            mac.update(cipher)
        dst.write(mac.digest())


def is_encrypted_stream(path: str) -> Optional[str]:
    magic = read_magic(path)
    if magic == MAGIC_V2:
        return "v2"
    if magic == MAGIC_V1:
        return "v1"
    return None


# ── Small sealed blobs (recovery kit) ────────────────────────────────────────
# Same primitives as the stream format, for a few kilobytes of JSON:
#   "ALVAKIT1." + base64url(header || ciphertext)
#   header = log2_n(1) r(1) p(1) salt(16) nonce(12); aad = b"ALVAKIT1" + header

KIT_PREFIX = "ALVAKIT1."
_KIT_HEADER = struct.Struct(">BBB16s12s")


def seal_json(payload: dict, passphrase: str) -> str:
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    header = _KIT_HEADER.pack(DEFAULT_LOG2_N, DEFAULT_R, DEFAULT_P, salt, nonce)
    key = derive_master_key(passphrase, salt, DEFAULT_LOG2_N, DEFAULT_R, DEFAULT_P)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sealed = AESGCM(key).encrypt(nonce, plaintext, b"ALVAKIT1" + header)
    return KIT_PREFIX + base64.urlsafe_b64encode(header + sealed).decode("ascii").rstrip("=")


def open_json(blob: str, passphrase: str) -> dict:
    import binascii
    import json
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    text = "".join(str(blob or "").split())   # tolerate line breaks from copy/paste
    if not text.startswith(KIT_PREFIX):
        raise BuddyCryptoError("This is not an AlvaOS recovery kit")
    body = text[len(KIT_PREFIX):]
    if len(body) > 256 * 1024:
        raise BuddyCryptoError("Recovery kit is too large")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise BuddyCryptoError("Recovery kit is damaged (not valid base64)") from None
    if len(raw) < _KIT_HEADER.size + 16:
        raise BuddyCryptoError("Recovery kit is damaged (too short)")
    header, sealed = raw[:_KIT_HEADER.size], raw[_KIT_HEADER.size:]
    log2_n, r, p, salt, nonce = _KIT_HEADER.unpack(header)
    key = derive_master_key(passphrase, salt, log2_n, r, p)
    try:
        plaintext = AESGCM(key).decrypt(nonce, sealed, b"ALVAKIT1" + header)
    except InvalidTag:
        raise BuddyCryptoError("Wrong password, or the recovery kit was modified") from None
    data = json.loads(plaintext.decode("utf-8"))
    if not isinstance(data, dict):
        raise BuddyCryptoError("Recovery kit has an unexpected format")
    return data


# ── Vault key blob ───────────────────────────────────────────────────────────
# The random key that unlocks a Buddy Backup vault (LUKS) is stored on the buddy
# next to the vault, sealed with the NAS's encryption key material:
#   "ALVAVKEY1." + base64url(header || AES-GCM(wrap_key, vault_key))
#   header = log2_n(1) r(1) p(1) kdf_salt(16) nonce(12)
# The machine that made it re-seals it without asking for the password (it
# keeps the scrypt output); a replacement machine derives the same key from
# the password and the public parameters in the header.
VAULT_KEY_PREFIX = "ALVAVKEY1."
_VKEY_HEADER = struct.Struct(">BBB16s12s")


def _vault_wrap_key(master_key: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"alvaos-buddy-vault-key-v1").derive(master_key)


def seal_vault_key(vault_key: bytes, material: KeyMaterial) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    header = _VKEY_HEADER.pack(material.log2_n, material.r, material.p, material.kdf_salt, nonce)
    sealed = AESGCM(_vault_wrap_key(material.master_key)).encrypt(nonce, vault_key, b"ALVAVKEY1" + header)
    return VAULT_KEY_PREFIX + base64.urlsafe_b64encode(header + sealed).decode("ascii").rstrip("=")


def vault_key_salt(blob: str) -> bytes:
    """The key-derivation salt a blob was sealed with (to notice a password change)."""
    raw = _vault_blob_bytes(blob)
    return _VKEY_HEADER.unpack(raw[:_VKEY_HEADER.size])[3]


def _vault_blob_bytes(blob: str) -> bytes:
    import binascii

    text = str(blob or "").strip()
    if not text.startswith(VAULT_KEY_PREFIX) or len(text) > 4096:
        raise BuddyCryptoError("The vault key stored on the buddy is not valid")
    body = text[len(VAULT_KEY_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (binascii.Error, ValueError):
        raise BuddyCryptoError("The vault key stored on the buddy is damaged") from None
    if len(raw) < _VKEY_HEADER.size + 16:
        raise BuddyCryptoError("The vault key stored on the buddy is damaged")
    return raw


def open_vault_key(blob: str, passphrase: str = "", material: Optional[KeyMaterial] = None) -> bytes:
    """Unseal with the stored key material (same machine) or the password (any machine)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = _vault_blob_bytes(blob)
    header, sealed = raw[:_VKEY_HEADER.size], raw[_VKEY_HEADER.size:]
    log2_n, r, p, salt, nonce = _VKEY_HEADER.unpack(header)
    if material is not None and material.kdf_salt == salt:
        master_key = material.master_key
    elif passphrase:
        master_key = derive_master_key(passphrase, salt, log2_n, r, p)
    else:
        raise BuddyCryptoError("The encryption password is needed to unlock this vault")
    try:
        return AESGCM(_vault_wrap_key(master_key)).decrypt(nonce, sealed, b"ALVAVKEY1" + header)
    except InvalidTag:
        raise BuddyCryptoError("Wrong encryption password for this vault") from None
