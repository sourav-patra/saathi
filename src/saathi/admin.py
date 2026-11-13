"""The owner's admin commands (M24; entry `saathi-admin`; RQ-95a, 110, 111a; PL-32, 33, 34).

Every command runs as the `saathi_owner` role on the host shell, names the household it changes, and writes an audit row
`system:owner_cli` in the same transaction as the change. Nothing here prints a key, a secret or a raw sender address:
sender addresses are sealed to the household content key and only an opaque reference is kept.

An audit row is keyed (hashed under the household `audit` key) as soon as that key exists. The three commands that run
before `keys init` (household, elder, approver) have no audit key yet, so their row is written unkeyed: codes only, no
subject, no payload hash.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import asyncpg
from pydantic import ValidationError

from saathi.audit.sink import PgAuditSink
from saathi.clock import SystemClock
from saathi.config import ConfigError, get_settings, read_secret_file
from saathi.domain import AuditEvent, Elder
from saathi.errors import KeyMissing, ReceiptMissing, SaathiError
from saathi.householdcfg import HouseholdConfig
from saathi.ids import ElderId, HouseholdId, PersonRef, RunId, new_id, normalize_e164
from saathi.ports import Clock, Tx
from saathi.rides.recognise import normalise
from saathi.store.db import Role, Store, create_pool
from saathi.store.keys import KEY_LEN, KeyRing, generate_content_pair, generate_secret, sender_hmac
from saathi.store.repos import ConfigRepo, ConsentEvent, ConsentRepo, IdentityRepo, SignoffRepo, VoiceRepo
from saathi.store.repos._util import aad, content_key_id
from saathi.store.repos.voice import VOICE_SCOPE

OWNER_ACTOR = "system:owner_cli"
GRANTER = "owner"
CHANNELS = ("whatsapp", "telegram_elder", "caregiver_forward")
# Channels whose platform terms must be on record before an identity is registered (PL-32), and channels whose address
# is a phone number. Data, not branches on a platform name (the core-module check in test_channels_contract).
TERMS_GATED = frozenset({"whatsapp"})
PHONE_CHANNELS = frozenset({"whatsapp"})
ROTATABLE = ("content", "audit")
TERMS_RECEIPT_ID = "pl-32"
TERMS_VALID_DAYS = 90
DEFAULT_RECEIPTS_DIR = Path("docs/evidence/receipts")
UNKEYED = "unkeyed"
TERMS_REFUSAL = "WhatsApp needs a valid terms receipt that allows it (PL-32); use the Telegram channel"
LANGUAGES: tuple[str, ...] = tuple(Elder.model_fields["languages"].default)  # the domain's language set

Purpose = Literal["content", "audit", "totp"]


class AdminError(SaathiError):
    """A refusal the owner can act on. The message names the problem and never a value."""


# Audit event names (the sink stores text; these are owner-CLI names).
EV_HOUSEHOLD = "admin_household_created"
EV_ELDER = "admin_elder_added"
EV_APPROVER = "admin_approver_added"
EV_KEYS_INIT = "admin_keys_initialised"
EV_KEYS_ROTATED = "key_rotated"
EV_KEYS_REWRAPPED = "admin_keys_rewrapped"
EV_IDENTITY = "admin_identity_added"
EV_SIGNOFF_GRANT = "admin_signoff_granted"
EV_SIGNOFF_REVOKE = "admin_signoff_revoked"
EV_CONSENT = "admin_consent_recorded"
EV_CONFIG = "admin_config_set"
EV_CLIP_REVIEW = "admin_clip_reviewed"
EV_PLACE_ADDED = "admin_place_added"
EV_PLACE_ALIAS = "admin_place_alias_added"
EV_PLACE_REMOVED = "admin_place_removed"


@dataclass(frozen=True)
class ReceiptInfo:
    sentence: str
    fields: dict[str, str]


def read_receipt(path: Path) -> ReceiptInfo:
    """A manual receipt: one plain sentence, then ``key: value`` lines. `ReceiptMissing` when absent or malformed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReceiptMissing() from exc
    if not lines or not lines[0].strip() or lines[0].startswith("#") or re.match(r"^[a-z_0-9]+:", lines[0]):
        raise ReceiptMissing()
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        key = key.strip().lstrip("-* ").strip("`")
        if sep and re.fullmatch(r"[a-z_0-9]+", key):
            fields[key] = value.strip().strip("`")
    return ReceiptInfo(sentence=lines[0].strip(), fields=fields)


def terms_receipt_allows(receipts_dir: Path, today: date) -> bool:
    """PL-32: the newest platform terms receipt is dated within 90 days, signed, verified and decides ``allowed``.

    A missing directory, a missing or stale receipt, or any other decision (not allowed, unclear) is False.
    """
    pattern = re.compile(rf"^RECEIPT-{TERMS_RECEIPT_ID}-(\d{{4}}-\d{{2}}-\d{{2}})\.md$")
    try:
        found = [(m.group(1), p) for p in receipts_dir.glob("RECEIPT-*.md") if (m := pattern.match(p.name))]
    except OSError:
        return False
    if not found:
        return False
    try:
        info = read_receipt(max(found)[1])
        when = date.fromisoformat(info.fields.get("date", ""))
    except (ReceiptMissing, ValueError):
        return False
    f = info.fields
    age = (today - when).days
    return (
        f.get("id", "").lower() == TERMS_RECEIPT_ID
        and 0 <= age <= TERMS_VALID_DAYS
        and bool(f.get("signer"))
        and f.get("result") == "verified"
        and f.get("decision") == "allowed"
    )


class Admin:
    """The commands as methods. `store` must be an owner-role store; any other role is refused at construction."""

    def __init__(
        self,
        store: Store,
        keys: KeyRing,
        clock: Clock,
        *,
        sender_key: bytes | None = None,
        receipts_dir: Path = DEFAULT_RECEIPTS_DIR,
    ) -> None:
        if store.role is not Role.OWNER:
            raise PermissionError("the admin commands run as the owner role only")
        self._store = store
        self._keys = keys
        self._clock = clock
        self._sender_key = sender_key
        self._receipts_dir = receipts_dir
        self._run = RunId(new_id())

    # -- audit ------------------------------------------------------------------------------------------------

    def _event(self, tx: Tx, event: str, subject: str | None, detail: dict[str, int | bool | str]) -> AuditEvent:
        return AuditEvent(
            household=tx.household, run_id=self._run, actor=OWNER_ACTOR, event=event, subject=subject, detail=detail
        )

    async def _audit(
        self,
        tx: Tx,
        event: str,
        *,
        subject: str | None = None,
        detail: dict[str, int | bool | str] | None = None,
        keys: KeyRing | None = None,
    ) -> None:
        """Keyed through the sink when the household has an audit key; unkeyed (codes only) before `keys init`."""
        ev = self._event(tx, event, subject, detail or {})
        has_key = await tx.fetch(
            "SELECT 1 FROM control.household_keys WHERE household_id = $1 AND purpose = 'audit' AND retired_at IS NULL",
            tx.household,
        )
        if has_key:
            await PgAuditSink(self._store, keys or self._keys).append(ev, tx=tx)
            return
        await tx.fetch(
            "SELECT control.audit_append($1, $2, $3, NULL, NULL, $4, $5::jsonb)",
            str(ev.run_id),
            ev.actor,
            ev.event,
            UNKEYED,
            dict(ev.detail),
        )

    # -- step 1: people ---------------------------------------------------------------------------------------

    async def household_create(self, household: str, *, name: str) -> None:
        async with self._store.tx(household=HouseholdId(household)) as tx:
            tag = await tx.execute(
                "INSERT INTO households (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING", household, name
            )
            if not tag.endswith(" 1"):
                raise AdminError("that household already exists")
            await self._audit(tx, EV_HOUSEHOLD, detail={"household": household})

    async def elder_add(self, household: str, elder: str, *, name: str, lang: str = "hi") -> None:
        if lang not in LANGUAGES:
            raise AdminError(f"language must be one of {', '.join(LANGUAGES)}")
        languages = [lang] if lang == "en" else [lang, "en"]
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            tag = await tx.execute(
                "INSERT INTO elders (id, household_id, display_name, languages) VALUES ($1, $2, $3, $4)"
                " ON CONFLICT DO NOTHING",
                elder,
                household,
                name,
                languages,
            )
            if not tag.endswith(" 1"):
                raise AdminError("that elder id is already in use")
            await self._audit(tx, EV_ELDER, subject=elder, detail={"elder": elder, "lang": lang})

    async def approver_add(
        self, household: str, approver: str, *, telegram_id: int, primary: bool = False, name: str | None = None
    ) -> None:
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            tag = await tx.execute(
                "INSERT INTO approvers (id, household_id, name, telegram_user_id, is_primary)"
                " VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                approver,
                household,
                name or approver,
                telegram_id,
                primary,
            )
            if not tag.endswith(" 1"):
                raise AdminError("that approver id or chat is already registered")
            await self._audit(tx, EV_APPROVER, subject=approver, detail={"approver": approver, "primary": primary})

    @staticmethod
    async def _need_household(tx: Tx) -> None:
        if not await tx.fetch("SELECT 1 FROM households WHERE id = $1", tx.household):
            raise AdminError("no such household")

    # -- step 2: keys -----------------------------------------------------------------------------------------

    def _new_key(self, household: str, purpose: Purpose) -> tuple[str, bytes | None, bytes]:
        """(key_id, public_key or None, wrapped_key) of a fresh key. `AdminError` when the KEK is not available."""
        key_id = new_id()[:12]
        try:
            if purpose == "content":
                public, private = generate_content_pair()
                return (
                    key_id,
                    bytes(public),
                    self._keys.wrap(private, household=HouseholdId(household), purpose=purpose, key_id=key_id),
                )
            return (
                key_id,
                None,
                self._keys.wrap(generate_secret(), household=HouseholdId(household), purpose=purpose, key_id=key_id),
            )
        except KeyMissing:
            raise AdminError("the key-encryption key is not available on this host") from None

    @staticmethod
    async def _insert_key(tx: Tx, purpose: str, key_id: str, public: bytes | None, wrapped: bytes) -> None:
        await tx.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, public_key, wrapped_key)"
            " VALUES ($1, $2, $3, $4, $5)",
            tx.household,
            purpose,
            key_id,
            public,
            wrapped,
        )

    async def keys_init(self, household: str) -> dict[str, str]:
        """Three keys for a household that has none. Returns purpose to key id; no key material leaves this method."""
        made: dict[str, str] = {}
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            if await tx.fetch("SELECT 1 FROM control.household_keys WHERE household_id = $1", household):
                raise AdminError("this household already has keys")
            purposes: tuple[Purpose, ...] = ("content", "audit", "totp")
            for purpose in purposes:
                key_id, public, wrapped = self._new_key(household, purpose)
                await self._insert_key(tx, purpose, key_id, public, wrapped)
                made[purpose] = key_id
            await self._audit(
                tx, EV_KEYS_INIT, detail={"household": household, **{f"{p}_key": k for p, k in made.items()}}
            )
        return made

    async def keys_rotate(self, household: str, purpose: str) -> str:
        """Retire the active key of this purpose and insert a new one in one transaction. Returns the new key id."""
        if purpose not in ROTATABLE:
            raise AdminError(f"only {' or '.join(ROTATABLE)} keys rotate")
        async with self._store.tx(household=HouseholdId(household)) as tx:
            active = await tx.fetch(
                "SELECT key_id FROM control.household_keys WHERE household_id = $1 AND purpose = $2"
                " AND retired_at IS NULL",
                household,
                purpose,
            )
            if not active:
                raise AdminError("no active key of that purpose; run keys init")
            key_id, public, wrapped = self._new_key(household, purpose)  # type: ignore[arg-type]
            await tx.execute(
                "UPDATE control.household_keys SET retired_at = $3"
                " WHERE household_id = $1 AND purpose = $2 AND retired_at IS NULL",
                household,
                purpose,
                tx.now,
            )
            await self._insert_key(tx, purpose, key_id, public, wrapped)
            await self._audit(
                tx,
                EV_KEYS_ROTATED,
                detail={"purpose": purpose, "old_key": str(active[0]["key_id"]), "new_key": key_id},
            )
        return key_id

    async def keys_rewrap(self, old_kek: bytes, new_kek: bytes) -> int:
        """Re-wrap every household key under a new KEK. Returns the number of rows changed.

        Two passes: the first opens every row (under the old KEK, or already under the new one) and builds the new
        wraps, and refuses before any write when a row opens under neither. The second writes. A run stopped half way
        is safe to repeat: rows already under the new KEK are skipped.

        Writes use one transaction per household (not one for all): a failure part way leaves the households already
        written under the new KEK, each with its audit row, and a rerun completes the rest.
        """
        old, new = KeyRing(old_kek), KeyRing(new_kek)
        if old_kek == new_kek:
            raise AdminError("the old and new key-encryption keys are the same")
        async with self._store.tx(household=VOICE_SCOPE) as tx:
            households = [str(r["id"]) for r in await tx.fetch("SELECT id FROM households ORDER BY id")]
        plan: dict[str, list[tuple[str, str, bytes, bytes]]] = {}
        for h in households:
            async with self._store.tx(household=HouseholdId(h)) as tx:
                rows = await tx.fetch(
                    "SELECT purpose, key_id, wrapped_key FROM control.household_keys WHERE household_id = $1", h
                )
            for r in rows:
                purpose, key_id, wrapped = str(r["purpose"]), str(r["key_id"]), bytes(r["wrapped_key"])
                try:
                    secret = old.unwrap(wrapped, household=HouseholdId(h), purpose=purpose, key_id=key_id)
                except KeyMissing:
                    try:
                        new.unwrap(wrapped, household=HouseholdId(h), purpose=purpose, key_id=key_id)
                    except KeyMissing:
                        raise AdminError(
                            "a stored key opens under neither key-encryption key; nothing changed"
                        ) from None
                    continue  # already under the new KEK
                plan.setdefault(h, []).append(
                    (
                        purpose,
                        key_id,
                        wrapped,
                        new.wrap(secret, household=HouseholdId(h), purpose=purpose, key_id=key_id),
                    )
                )
        # One transaction per household: a failure part-way leaves the earlier households rewrapped, and a rerun
        # finishes the rest (rows already under the new KEK are skipped).
        changed = 0
        for h, items in plan.items():
            async with self._store.tx(household=HouseholdId(h)) as tx:
                for purpose, key_id, old_wrapped, new_wrapped in items:
                    tag = await tx.execute(
                        "UPDATE control.household_keys SET wrapped_key = $5"
                        " WHERE household_id = $1 AND purpose = $2 AND key_id = $3 AND wrapped_key = $4",
                        h,
                        purpose,
                        key_id,
                        old_wrapped,
                        new_wrapped,
                    )
                    if not tag.endswith(" 1"):
                        raise AdminError("a stored key changed during the rewrap; run it again")
                    changed += 1
                await self._audit(tx, EV_KEYS_REWRAPPED, detail={"rows": len(items)}, keys=new)
        return changed

    # -- step 3: identity -------------------------------------------------------------------------------------

    async def identity_add(self, household: str, elder: str, *, channel: str, sender: str) -> str:
        """Register an elder's address on a channel. Returns the opaque person reference (never the address)."""
        if channel not in CHANNELS:
            raise AdminError(f"channel must be one of {', '.join(CHANNELS)}")
        if channel in TERMS_GATED and not terms_receipt_allows(self._receipts_dir, self._clock.now().date()):
            raise AdminError(TERMS_REFUSAL)
        if self._sender_key is None:
            raise AdminError("the sender hash key is not available on this host")
        try:
            address = normalize_e164(sender) if channel in PHONE_CHANNELS else sender.strip()
        except ValueError:
            raise AdminError("the sender is not a valid address for that channel") from None
        if not address:
            raise AdminError("the sender is empty")
        async with self._store.tx(household=HouseholdId(household)) as tx:
            try:
                public = await self._keys.public(tx, "content")
            except KeyMissing:
                raise AdminError("this household has no keys yet; run keys init first") from None
            if not await tx.fetch("SELECT 1 FROM elders WHERE id = $1 AND household_id = $2", elder, household):
                raise AdminError("no such elder in this household")
            key_id = await content_key_id(tx, public)
            identity_id, ref = new_id(), PersonRef(new_id())
            sealed = self._keys.seal(public, address.encode(), aad=aad(household, identity_id, "ref"))
            ok = await IdentityRepo.register(
                tx,
                ElderId(elder),
                channel,
                sender_hmac=sender_hmac(self._sender_key, channel, address),
                person_ref=ref,
                person_ref_enc=sealed,
                key_id=key_id,
                identity_id=identity_id,
            )
            if not ok:
                raise AdminError("that identity is already registered")
            await self._audit(tx, EV_IDENTITY, subject=elder, detail={"channel": channel, "identity": identity_id})
        return str(ref)

    # -- step 4: sign-offs, consent, config, clips ------------------------------------------------------------

    async def signoff_grant(self, household: str, scope: str, *, days: int) -> int:
        if scope != "SO-1":
            raise AdminError("the only sign-off scope is SO-1")
        if days <= 0:
            raise AdminError("days must be positive")
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            sid = await SignoffRepo.grant(tx, "SO-1", granter=GRANTER, expires_at=tx.now + timedelta(days=days))
            await self._audit(tx, EV_SIGNOFF_GRANT, detail={"scope": scope, "signoff": sid, "days": days})
        return sid

    async def signoff_revoke(self, household: str, scope: str) -> int:
        if scope != "SO-1":
            raise AdminError("the only sign-off scope is SO-1")
        async with self._store.tx(household=HouseholdId(household)) as tx:
            state = await SignoffRepo.valid(tx, "SO-1", tx.now)
            if state.id is None or state.revoked or not await SignoffRepo.revoke(tx, state.id, tx.now):
                raise AdminError("no sign-off to revoke")
            await self._audit(tx, EV_SIGNOFF_REVOKE, detail={"scope": scope, "signoff": state.id})
        return state.id

    async def consent_record(self, household: str, path: Path) -> None:
        data = _read_json_object(path)
        try:
            ev = ConsentEvent.model_validate(data)
        except ValidationError as exc:
            raise AdminError(f"the consent record is invalid: {_fields(exc)}") from None
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            await ConsentRepo.append(tx, ev)
            await self._audit(
                tx, EV_CONSENT, detail={"subject": ev.subject, "event": ev.event, "capacity": ev.capacity}
            )

    async def config_set(self, household: str, path: Path) -> int:
        """Append a config version from a JSON file. A file with no ``version`` becomes the next one. Returns it."""
        data = _read_json_object(path)
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            if "version" in data:
                version = data.pop("version")
            else:
                rows = await tx.fetch(
                    "SELECT coalesce(max(version), 0) AS v FROM control.household_config WHERE household_id = $1",
                    household,
                )
                version = int(rows[0]["v"]) + 1
            try:
                cfg = HouseholdConfig.model_validate({**data, "version": version})
            except ValidationError as exc:
                raise AdminError(f"the config is invalid: {_fields(exc)}") from None
            gated = {cfg.elder_channel_active, *cfg.channels_enabled} & TERMS_GATED
            if gated and not terms_receipt_allows(self._receipts_dir, self._clock.now().date()):
                raise AdminError(TERMS_REFUSAL)
            if not await ConfigRepo.append(tx, cfg, created_by=GRANTER):
                raise AdminError("that config version already exists")
            await self._audit(tx, EV_CONFIG, detail={"version": cfg.version})
        return cfg.version

    async def clips_review(
        self, household: str, reply_key: str, version: int, *, receipt: Path, language: str = "hi"
    ) -> None:
        """Mark a fixed clip reviewed. The dated manual receipt must exist, name a date and a signer (D-71).

        `household` only attributes the audit row: `voice_clips` is shared and the audit log needs a household.
        """
        info = read_receipt(receipt)  # ReceiptMissing before any write
        signer = info.fields.get("signer", "")
        try:
            date.fromisoformat(info.fields.get("date", ""))
        except ValueError:
            raise ReceiptMissing() from None
        if not signer:
            raise ReceiptMissing()
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            ok = await VoiceRepo.review(
                tx,
                reply_key=reply_key,
                version=version,
                language=language,
                reviewer=signer,
                at=tx.now,
                receipt_ref=receipt.name,
            )
            if not ok:
                raise AdminError("no such clip version")
            await self._audit(
                tx, EV_CLIP_REVIEW, detail={"reply_key": reply_key, "version": version, "language": language}
            )

    # -- ride places: the saved places the ride helper matches (rows of `addresses`) ----------------------------

    @staticmethod
    async def _find_place(tx: Tx, ref: str) -> str:
        """The id of the place named by an id or a label (case folded). `AdminError` when none or several match."""
        rows = await tx.fetch("SELECT id FROM addresses WHERE id = $1 OR lower(label) = lower($1)", ref)
        ids = sorted({str(r["id"]) for r in rows})
        if not ids:
            raise AdminError("no such place")
        if len(ids) > 1:
            raise AdminError("more than one place has that label; use the id")
        return ids[0]

    async def place_add(
        self,
        household: str,
        *,
        label: str,
        address: str,
        lat: float | None = None,
        lng: float | None = None,
        aliases: Sequence[str] = (),
        pickup: bool = False,
    ) -> str:
        """Save a place. Returns its id. At most one place of a household is the pickup."""
        label, address = label.strip(), address.strip()
        if not label or not address:
            raise AdminError("a place needs a label and an address")
        if (lat is None) != (lng is None):
            raise AdminError("give both --lat and --lng, or neither")
        if lat is not None and lng is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise AdminError("the coordinates are out of range")
        names = [a.strip() for a in aliases]
        if not normalise(label) or not all(normalise(a) for a in names):
            raise AdminError("a label and an alias need at least one letter or digit")
        place = new_id()
        try:
            async with self._store.tx(household=HouseholdId(household)) as tx:
                await self._need_household(tx)
                if pickup and await tx.fetch("SELECT 1 FROM addresses WHERE is_pickup"):
                    raise AdminError("this household already has a pickup place")
                await tx.execute(
                    "INSERT INTO addresses (id, household_id, label, formatted, lat, lng, aliases, is_pickup)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    place, household, label, address, lat, lng, names, pickup,
                )  # fmt: skip
                await self._audit(tx, EV_PLACE_ADDED, subject=place, detail={"place": place, "pickup": pickup})
        except asyncpg.UniqueViolationError:
            raise AdminError("this household already has a pickup place") from None
        return place

    async def place_list(self, household: str) -> list[str]:
        """One line per place: id, label, pickup, coordinates, rides, aliases. The address itself is not printed."""
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            rows = await tx.fetch(
                "SELECT id, label, lat, lng, aliases, is_pickup, ride_count FROM addresses ORDER BY label, id"
            )
        return [
            f"{r['id']}  {r['label']}  pickup={'yes' if r['is_pickup'] else 'no'}"
            f"  coordinates={'yes' if r['lat'] is not None and r['lng'] is not None else 'no'}"
            f"  rides={r['ride_count']}  aliases={', '.join(r['aliases']) or '-'}"
            for r in rows
        ]

    async def place_alias_add(self, household: str, place: str, alias: str) -> None:
        alias = alias.strip()
        if not normalise(alias):
            raise AdminError("an alias needs at least one letter or digit")
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            place_id = await self._find_place(tx, place)
            tag = await tx.execute(
                "UPDATE addresses SET aliases = array_append(aliases, $2) WHERE id = $1 AND NOT ($2 = ANY(aliases))",
                place_id,
                alias,
            )
            if not tag.endswith(" 1"):
                raise AdminError("that place already has this alias")
            await self._audit(tx, EV_PLACE_ALIAS, subject=place_id, detail={"place": place_id})

    async def place_remove(self, household: str, place: str) -> None:
        async with self._store.tx(household=HouseholdId(household)) as tx:
            await self._need_household(tx)
            place_id = await self._find_place(tx, place)
            await tx.execute("DELETE FROM addresses WHERE id = $1", place_id)
            await self._audit(tx, EV_PLACE_REMOVED, subject=place_id, detail={"place": place_id})


def _fields(exc: ValidationError) -> str:
    """Field names only: a validation message can echo a value."""
    return ", ".join(sorted({".".join(str(p) for p in e["loc"]) or "(root)" for e in exc.errors()}))


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdminError("the file cannot be read as JSON") from None
    if not isinstance(data, dict):
        raise AdminError("the file must hold one JSON object")
    return data


# -- command line ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="saathi-admin", description="Owner commands. Run on the host shell only.")
    p.add_argument("--receipts-dir", type=Path, default=DEFAULT_RECEIPTS_DIR, help="where the manual receipts live")
    top = p.add_subparsers(dest="group", required=True)

    h = top.add_parser("household").add_subparsers(dest="cmd", required=True)
    c = h.add_parser("create")
    c.add_argument("household")
    c.add_argument("--name", required=True)

    e = top.add_parser("elder").add_subparsers(dest="cmd", required=True)
    c = e.add_parser("add")
    c.add_argument("household")
    c.add_argument("elder")
    c.add_argument("--name", required=True)
    c.add_argument("--lang", default="hi", choices=LANGUAGES)

    a = top.add_parser("approver").add_subparsers(dest="cmd", required=True)
    c = a.add_parser("add")
    c.add_argument("household")
    c.add_argument("approver")
    c.add_argument("--telegram-id", type=int, required=True)
    c.add_argument("--primary", action="store_true")
    c.add_argument("--name")

    k = top.add_parser("keys").add_subparsers(dest="cmd", required=True)
    c = k.add_parser("init")
    c.add_argument("household")
    c = k.add_parser("rotate")
    c.add_argument("household")
    c.add_argument("purpose", choices=ROTATABLE)
    c = k.add_parser("rewrap")
    c.add_argument("--old-file", type=Path, required=True)
    c.add_argument("--new-file", type=Path, required=True)

    i = top.add_parser("identity").add_subparsers(dest="cmd", required=True)
    c = i.add_parser("add")
    c.add_argument("household")
    c.add_argument("elder")
    c.add_argument("--channel", required=True, choices=CHANNELS)
    c.add_argument("--sender", required=True)

    s = top.add_parser("signoff").add_subparsers(dest="cmd", required=True)
    c = s.add_parser("grant")
    c.add_argument("household")
    c.add_argument("scope")
    c.add_argument("--days", type=int, required=True)
    c = s.add_parser("revoke")
    c.add_argument("household")
    c.add_argument("scope")

    n = top.add_parser("consent").add_subparsers(dest="cmd", required=True)
    c = n.add_parser("record")
    c.add_argument("household")
    c.add_argument("--file", type=Path, required=True)

    g = top.add_parser("config").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("set")
    c.add_argument("household")
    c.add_argument("--file", type=Path, required=True)

    v = top.add_parser("clips").add_subparsers(dest="cmd", required=True)
    c = v.add_parser("review")
    c.add_argument("reply_key")
    c.add_argument("version", type=int)
    c.add_argument("--receipt", type=Path, required=True)
    c.add_argument("--household", required=True, help="household named on the audit row")
    c.add_argument("--language", default="hi")

    pl = top.add_parser("place").add_subparsers(dest="cmd", required=True)
    c = pl.add_parser("add")
    c.add_argument("--household", required=True)
    c.add_argument("--label", required=True)
    c.add_argument("--address", required=True)
    c.add_argument("--lat", type=float)
    c.add_argument("--lng", type=float)
    c.add_argument("--alias", action="append", default=[], help="another name for the place; repeat for several")
    c.add_argument("--pickup", action="store_true", help="rides start here (one per household)")
    c = pl.add_parser("list")
    c.add_argument("--household", required=True)
    c = pl.add_parser("alias-add")
    c.add_argument("--household", required=True)
    c.add_argument("--place", required=True, help="the place id, or its label")
    c.add_argument("--alias", required=True)
    c = pl.add_parser("remove")
    c.add_argument("--household", required=True)
    c.add_argument("--place", required=True, help="the place id, or its label")
    return p


def _secret(path: str, what: str) -> bytes:
    if not path:
        raise AdminError(f"{what} is not set")
    try:
        return read_secret_file(path)
    except ConfigError:
        raise AdminError(f"{what} cannot be read") from None


async def dispatch(args: argparse.Namespace, admin: Admin) -> str:
    """Run one parsed command and return the line to print. Never a key, a secret or an address."""
    match (args.group, args.cmd):
        case ("household", "create"):
            await admin.household_create(args.household, name=args.name)
            return f"household created: {args.household}"
        case ("elder", "add"):
            await admin.elder_add(args.household, args.elder, name=args.name, lang=args.lang)
            return f"elder added: {args.elder}"
        case ("approver", "add"):
            await admin.approver_add(
                args.household, args.approver, telegram_id=args.telegram_id, primary=args.primary, name=args.name
            )
            return f"approver added: {args.approver}"
        case ("keys", "init"):
            made = await admin.keys_init(args.household)
            return "\n".join(f"{p} key_id: {k}" for p, k in made.items())
        case ("keys", "rotate"):
            return f"{args.purpose} key_id: {await admin.keys_rotate(args.household, args.purpose)}"
        case ("identity", "add"):
            ref = await admin.identity_add(args.household, args.elder, channel=args.channel, sender=args.sender)
            return f"identity added: person_ref {ref}"
        case ("signoff", "grant"):
            return f"sign-off granted: id {await admin.signoff_grant(args.household, args.scope, days=args.days)}"
        case ("signoff", "revoke"):
            return f"sign-off revoked: id {await admin.signoff_revoke(args.household, args.scope)}"
        case ("consent", "record"):
            await admin.consent_record(args.household, args.file)
            return "consent recorded"
        case ("config", "set"):
            return f"config version {await admin.config_set(args.household, args.file)} set"
        case ("clips", "review"):
            await admin.clips_review(
                args.household, args.reply_key, args.version, receipt=args.receipt, language=args.language
            )
            return f"clip reviewed: {args.reply_key} v{args.version}"
        case ("place", "add"):
            place = await admin.place_add(
                args.household, label=args.label, address=args.address, lat=args.lat, lng=args.lng,
                aliases=args.alias, pickup=args.pickup,
            )  # fmt: skip
            return f"place added: {place}"
        case ("place", "list"):
            return "\n".join(await admin.place_list(args.household)) or "no places"
        case ("place", "alias-add"):
            await admin.place_alias_add(args.household, args.place, args.alias)
            return "alias added"
        case ("place", "remove"):
            await admin.place_remove(args.household, args.place)
            return "place removed"
    raise AdminError("unknown command")  # pragma: no cover


async def _run(args: argparse.Namespace) -> str:
    s = get_settings()
    pool = await create_pool(s.db_url_owner, min_size=1, max_size=2)
    store = Store(pool, Role.OWNER, SystemClock())
    try:
        if (args.group, args.cmd) == ("keys", "rewrap"):
            admin = Admin(store, KeyRing(None), SystemClock())
            old = _secret(str(args.old_file), "the old key-encryption key file")
            new = _secret(str(args.new_file), "the new key-encryption key file")
            if len(old) != KEY_LEN or len(new) != KEY_LEN:
                raise AdminError("a key-encryption key must be 32 bytes")
            return f"rewrapped rows: {await admin.keys_rewrap(old, new)}"
        # Every command but rewrap needs the KEK: once a household has keys, each audit row is keyed under it.
        kek = _secret(s.kek_file, "SAATHI_KEK_FILE")
        sender = _secret(s.sender_hmac_key_file, "SAATHI_SENDER_HMAC_KEY_FILE") if args.group == "identity" else None
        try:
            ring = KeyRing(kek)
        except ValueError:
            raise AdminError("a key-encryption key must be 32 bytes") from None
        admin = Admin(store, ring, SystemClock(), sender_key=sender, receipts_dir=args.receipts_dir)
        return await dispatch(args, admin)
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(asyncio.run(_run(args)))
    except (AdminError, ReceiptMissing) as exc:
        print(f"error: {str(exc) or 'a required receipt is missing or incomplete'}", file=sys.stderr)
        return 2
    except ValidationError:
        print("error: invalid id", file=sys.stderr)
        return 2
    except asyncpg.PostgresError as exc:
        print(f"error: the database refused the change ({type(exc).__name__})", file=sys.stderr)
        return 1
    except SaathiError as exc:
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
