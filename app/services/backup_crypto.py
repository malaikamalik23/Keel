"""Passphrase encryption for backup files: scrypt + AES-256-GCM in chunks, streamed (nothing plaintext touches disk).

File layout:  header | chunk 0 | chunk 1 | ... | final chunk
    header  = b"KEELBAK1" | log2(N) (1 byte) | r (1) | p (1) | salt (16) | nonce prefix (7)          = 34 bytes
    chunk i = AES-GCM(key, nonce = prefix | i (4 bytes, big endian) | last (1 byte), plaintext <= 64 KiB, aad = header)

Every chunk is authenticated with the header as associated data and its own position, so a wrong passphrase, a changed
byte, reordered chunks, a swapped header or a cut-off file are all refused ("the STREAM construction"). The key is
derived from the passphrase with scrypt (N=2^17, r=8, p=1: ~128 MB and a fraction of a second per try), so guessing a
good passphrase is slow. The passphrase itself is never stored.
"""
from __future__ import annotations

import hashlib
import os
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"KEELBAK1"
HEADER_LEN = len(MAGIC) + 3 + 16 + 7
CHUNK = 64 * 1024
TAG = 16
SCRYPT_LOG_N = 17  # tests lower this to keep them fast; the value used is stored in each file's header
SCRYPT_R, SCRYPT_P = 8, 1
MIN_PASSPHRASE = 12


class BackupCryptoError(Exception):
    """Wrong passphrase, a damaged or cut-off file, or not a Keel backup."""


def check_passphrase(passphrase: str | None) -> str:
    if not passphrase or len(passphrase) < MIN_PASSPHRASE:
        raise BackupCryptoError(f"The backup passphrase must be at least {MIN_PASSPHRASE} characters (a few random words is best).")
    return passphrase


def _key(passphrase: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    if not (10 <= log_n <= 22 and 1 <= r <= 32 and 1 <= p <= 4):
        raise BackupCryptoError("This backup's key settings are out of range; it isn't a Keel backup or is damaged.")
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=1 << log_n, r=r, p=p, maxmem=(256 + 1) * 1024 * 1024, dklen=32)


def _nonce(prefix: bytes, i: int, last: bool) -> bytes:
    if i >= 1 << 32:
        raise BackupCryptoError("Backup too large.")
    return prefix + i.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


def is_encrypted(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


class EncryptingWriter:
    """A write-only file object: everything written is encrypted into `raw`. close() writes the final chunk."""

    def __init__(self, raw: BinaryIO, passphrase: str):
        salt, self._prefix = os.urandom(16), os.urandom(7)
        self._header = MAGIC + bytes([SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P]) + salt + self._prefix
        self._aead = AESGCM(_key(check_passphrase(passphrase), salt, SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P))
        self._raw, self._buf, self._i, self.closed = raw, bytearray(), 0, False
        raw.write(self._header)

    def write(self, data) -> int:
        self._buf += data
        while len(self._buf) > CHUNK:  # keep at least one byte back so the final chunk is never empty unless all is
            self._emit(bytes(self._buf[:CHUNK]), last=False)
            del self._buf[:CHUNK]
        return len(data)

    def _emit(self, plain: bytes, last: bool) -> None:
        self._raw.write(self._aead.encrypt(_nonce(self._prefix, self._i, last), plain, self._header))
        self._i += 1

    def flush(self) -> None:
        self._raw.flush()

    def close(self) -> None:
        if not self.closed:
            self._emit(bytes(self._buf), last=True)
            self._buf.clear()
            self._raw.flush()
            self.closed = True


class DecryptingReader:
    """A read-only file object over an encrypted backup. Raises BackupCryptoError on any tampering or truncation."""

    def __init__(self, raw: BinaryIO, passphrase: str):
        header = raw.read(HEADER_LEN)
        if len(header) < HEADER_LEN or not header.startswith(MAGIC):
            raise BackupCryptoError("This isn't an encrypted Keel backup.")
        log_n, r, p = header[8], header[9], header[10]
        salt, self._prefix = header[11:27], header[27:34]
        self._header = header
        self._aead = AESGCM(_key(passphrase or "", salt, log_n, r, p))
        self._raw, self._buf, self._i, self._done = raw, b"", 0, False
        self._next = raw.read(CHUNK + TAG)

    def _decrypt_next(self) -> None:
        sealed, self._next = self._next, self._raw.read(CHUNK + TAG)
        last = not self._next
        if len(sealed) < TAG:
            raise BackupCryptoError("The backup is cut off (incomplete file).")
        try:
            self._buf += self._aead.decrypt(_nonce(self._prefix, self._i, last), sealed, self._header)
        except InvalidTag:
            if self._i == 0:
                raise BackupCryptoError("Wrong passphrase, or the backup is damaged.") from None
            raise BackupCryptoError("The backup is damaged or cut off (a part failed its check).") from None
        self._i += 1
        self._done = last

    def read(self, n: int = -1) -> bytes:
        while not self._done and (n < 0 or len(self._buf) < n):
            self._decrypt_next()
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        pass
