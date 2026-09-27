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
from typing import Callable, Optional

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


def encrypt_file(plain_path: str, encrypted_path: str, material: KeyMaterial,
                 chunk_size: int = CHUNK_SIZE) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    stream_salt = secrets.token_bytes(32)
    nonce_prefix = secrets.token_bytes(7)
    header = HEADER_STRUCT.pack(
        MAGIC_V2, KDF_SCRYPT, material.log2_n, material.r, material.p,
        material.kdf_salt, stream_salt, nonce_prefix, chunk_size,
    )
    aead = AESGCM(_stream_key(material.master_key, stream_salt))
    counter = 0
    with open(plain_path, "rb") as src, open(encrypted_path, "wb") as dst:
        dst.write(header)
        chunk = src.read(chunk_size)
        while True:
            following = src.read(chunk_size)
            last = not following
            dst.write(aead.encrypt(_nonce(nonce_prefix, counter, last), chunk, header))
            if last:
                break
            chunk = following
            counter += 1


def decrypt_file(encrypted_path: str, plain_path: str,
                 master_key_for: Callable[[StreamHeader], bytes]) -> None:
    """Decrypt a stream. `master_key_for(header)` returns the master key.

    That callback either returns a stored key (same machine) or derives it
    from the passphrase with the parameters in the header (fresh machine).
    Raises BuddyCryptoError on a wrong key or any tampering; the partial
    output file is removed in that case.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        with open(encrypted_path, "rb") as src, open(plain_path, "wb") as dst:
            header = parse_header(src.read(HEADER_LEN))
            aead = AESGCM(_stream_key(master_key_for(header), header.stream_salt))
            sealed_len = header.chunk_size + TAG_LEN
            counter = 0
            sealed = src.read(sealed_len)
            while True:
                following = src.read(sealed_len)
                last = not following
                if len(sealed) < TAG_LEN:
                    raise BuddyCryptoError("Encrypted stream is truncated")
                try:
                    dst.write(aead.decrypt(_nonce(header.nonce_prefix, counter, last), sealed, header.raw))
                except InvalidTag:
                    if counter == 0:
                        raise BuddyCryptoError("Wrong encryption password, or the stream was modified") from None
                    raise BuddyCryptoError("Encrypted stream was modified or truncated") from None
                if last:
                    break
                sealed = following
                counter += 1
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
