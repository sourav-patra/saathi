"""KeyRing: envelope keys per household (M05; RQ-111a, RQ-56a).

One module seals, opens, wraps and keys hashes (data part, section 4). The key-encryption key (KEK) is held by the
worker process only: an edge builds ``KeyRing(kek=None)``, which can seal to the public half of the content key and
nothing else. No private key, secret key or KEK is logged, returned or stored by this module.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import os
from typing import Literal, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from saathi.errors import KeyMissing
from saathi.ids import HouseholdId, PublicKey
from saathi.ports import Tx

SEAL_VERSION = b"\x01"
SEAL_INFO = b"saathi.seal.v1"
NONCE_LEN = 12
KEY_LEN = 32
_RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw


class KeyReader(Protocol):
    """Reads key rows inside a household transaction. The edge role may read only `active_public`."""

    async def active_public(self, tx: Tx, purpose: str) -> tuple[str, bytes] | None:
        """(key_id, public_key) of the active key of this purpose, or None."""
        ...

    async def wrapped(self, tx: Tx, purpose: str, key_id: str | None) -> tuple[str, bytes] | None:
        """(key_id, wrapped_key) of the named key, or of the active one when `key_id` is None. Core and owner only."""
        ...


class TxKeyReader:
    """The default reader: `control.household_keys` through the household transaction (row-level security applies)."""

    async def active_public(self, tx: Tx, purpose: str) -> tuple[str, bytes] | None:
        rows = await tx.fetch(
            "SELECT key_id, public_key FROM control.household_keys"
            " WHERE household_id = $1 AND purpose = $2 AND retired_at IS NULL",
            tx.household,
            purpose,
        )
        if not rows or rows[0]["public_key"] is None:
            return None
        return str(rows[0]["key_id"]), bytes(rows[0]["public_key"])

    async def wrapped(self, tx: Tx, purpose: str, key_id: str | None) -> tuple[str, bytes] | None:
        if key_id is None:
            rows = await tx.fetch(
                "SELECT key_id, wrapped_key FROM control.household_keys"
                " WHERE household_id = $1 AND purpose = $2 AND retired_at IS NULL",
                tx.household,
                purpose,
            )
        else:
            rows = await tx.fetch(
                "SELECT key_id, wrapped_key FROM control.household_keys"
                " WHERE household_id = $1 AND purpose = $2 AND key_id = $3",
                tx.household,
                purpose,
                key_id,
            )
        if not rows:
            return None
        return str(rows[0]["key_id"]), bytes(rows[0]["wrapped_key"])


def sender_hmac(key: bytes, channel: str, sender: str) -> str:
    """Keyed hash of a sender under the deployment key (the household is unknown at the edge; data part 4.4)."""
    if ":" in channel:
        raise ValueError("a channel name cannot contain ':'")
    return _hmac.new(key, f"{channel}:{sender}".encode(), hashlib.sha256).hexdigest()


def generate_content_pair() -> tuple[PublicKey, bytes]:
    """A fresh X25519 pair as raw bytes: (public, private). The private half is for `KeyRing.wrap` and nothing else."""
    sk = X25519PrivateKey.generate()
    return PublicKey(sk.public_key().public_bytes(*_RAW)), sk.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )


def generate_secret() -> bytes:
    """32 random bytes for an `audit` or `totp` key."""
    return os.urandom(KEY_LEN)


def _wrap_aad(household: str, purpose: str, key_id: str) -> bytes:
    return f"{household}|{purpose}|{key_id}".encode()


def _seal_key(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=None, info=SEAL_INFO).derive(shared)


class KeyRing:
    """Implements `Sealer` and `Opener` of ports.py. Imports the store's `Tx` shape, never the reverse."""

    def __init__(self, kek: bytes | None, reader: KeyReader | None = None) -> None:
        if kek is not None and len(kek) != KEY_LEN:
            raise ValueError("the key-encryption key must be 32 bytes")
        self._kek = kek
        self._reader: KeyReader = reader or TxKeyReader()
        self._private: dict[tuple[str, str], X25519PrivateKey] = {}
        self._audit: dict[tuple[str, str], bytes] = {}

    def __repr__(self) -> str:
        return f"KeyRing(kek={'set' if self._kek else 'absent'})"

    async def public(self, tx: Tx, purpose: Literal["content"]) -> PublicKey:
        got = await self._reader.active_public(tx, purpose)
        if got is None:
            raise KeyMissing()
        return PublicKey(got[1])

    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes:
        """Sealed box to the public half: version byte, ephemeral public key, nonce, ciphertext and tag."""
        try:
            peer = X25519PublicKey.from_public_bytes(bytes(public))
        except ValueError as exc:
            raise KeyMissing() from exc
        eph = X25519PrivateKey.generate()
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(_seal_key(eph.exchange(peer))).encrypt(nonce, plaintext, aad)
        return SEAL_VERSION + eph.public_key().public_bytes(*_RAW) + nonce + ct

    def _need_kek(self) -> bytes:
        if self._kek is None:
            raise KeyMissing()
        return self._kek

    def unwrap(self, wrapped: bytes, *, household: HouseholdId, purpose: str, key_id: str) -> bytes:
        kek = self._need_kek()
        if len(wrapped) <= NONCE_LEN:
            raise KeyMissing()
        try:
            return AESGCM(kek).decrypt(wrapped[:NONCE_LEN], wrapped[NONCE_LEN:], _wrap_aad(household, purpose, key_id))
        except InvalidTag as exc:
            raise KeyMissing() from exc

    def wrap(self, secret: bytes, *, household: HouseholdId, purpose: str, key_id: str) -> bytes:
        """AES-256-GCM under the KEK, random nonce, AAD ``household|purpose|key_id``; nonce plus ciphertext."""
        kek = self._need_kek()
        nonce = os.urandom(NONCE_LEN)
        return nonce + AESGCM(kek).encrypt(nonce, secret, _wrap_aad(household, purpose, key_id))

    async def _content_private(self, tx: Tx, key_id: str) -> X25519PrivateKey:
        self._need_kek()
        cached = self._private.get((tx.household, key_id))
        if cached is not None:
            return cached
        got = await self._reader.wrapped(tx, "content", key_id)
        if got is None:
            raise KeyMissing()
        raw = self.unwrap(got[1], household=tx.household, purpose="content", key_id=got[0])
        sk = X25519PrivateKey.from_private_bytes(raw)
        self._private[(tx.household, key_id)] = sk
        return sk

    async def open(self, tx: Tx, blob: bytes, key_id: str, *, aad: bytes) -> bytes:
        """Core only. `ValueError` when the blob is malformed, belongs to another row or household, or was altered."""
        sk = await self._content_private(tx, key_id)
        if len(blob) < 1 + KEY_LEN + NONCE_LEN + 16 or blob[:1] != SEAL_VERSION:
            raise ValueError("sealed blob is malformed")
        eph = X25519PublicKey.from_public_bytes(blob[1 : 1 + KEY_LEN])
        nonce = blob[1 + KEY_LEN : 1 + KEY_LEN + NONCE_LEN]
        try:
            return AESGCM(_seal_key(sk.exchange(eph))).decrypt(nonce, blob[1 + KEY_LEN + NONCE_LEN :], aad)
        except InvalidTag as exc:
            raise ValueError("sealed blob does not open here") from exc

    async def hmac(self, tx: Tx, domain: str, value: str) -> tuple[str, str]:
        """(hex, key_id): HMAC-SHA256 over ``domain|value`` under the household `audit` key."""
        if "|" in domain:
            raise ValueError("a domain name cannot contain '|'")
        self._need_kek()
        got = await self._reader.wrapped(tx, "audit", None)
        if got is None:
            raise KeyMissing()
        key_id, wrapped = got
        key = self._audit.get((tx.household, key_id))
        if key is None:
            key = self.unwrap(wrapped, household=tx.household, purpose="audit", key_id=key_id)
            self._audit[(tx.household, key_id)] = key
        return _hmac.new(key, f"{domain}|{value}".encode(), hashlib.sha256).hexdigest(), key_id
