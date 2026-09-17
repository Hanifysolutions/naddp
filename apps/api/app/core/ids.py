"""Identifier generation.

Two *deliberately unrelated* kinds of identifier exist in this system:

``new_id()``
    The internal primary key. A ULID rendered into a UUID column, so rows are
    time-sortable (index locality, natural chronological ordering) while the
    database column stays a plain ``uuid``.

``new_public_ref()``
    The externally visible reference, e.g. the consular case number a citizen
    is given over the phone (``NADDP-7F3K9QX2-4M2W``).

``seed_id()``
    The primary key of a *seeded* row: a pure function of the row's stable slug,
    so the same slug yields the same id on every machine and every run. It lives
    here rather than in the seeder because two callers need the same answer --
    ``data/demo-seed`` when it writes the row, and ``app.services.outcomes``
    when it needs to find that row again. One implementation, so they cannot
    drift apart.

WHY THE PUBLIC REFERENCE MUST NEVER BE DERIVED FROM THE PRIMARY KEY
-------------------------------------------------------------------
A ULID encodes its creation timestamp and is monotonic within a millisecond.
If the public reference were derived from (or equal to) the primary key, then
anyone holding one reference could:

1. read the exact creation time of the record;
2. infer case volume and arrival rate by comparing two references;
3. enumerate neighbouring records by walking the sortable prefix.

For consular cases that is a privacy and enumeration hazard, so the public
reference is generated independently from ``secrets`` (CSPRNG), carries no
timestamp, and is stored in its own uniquely-indexed column. Uniqueness is a
database constraint, not an assumption: callers must retry on conflict.

WHY IT CARRIES NO CONTEXT TAG
-----------------------------
ADR-0007 requires the reference to be *opaque*: it encodes no timestamp, no
sequence, no case type, no mission and no citizen attribute, because any
structure is an inference channel. An earlier draft of this module took a
``prefix`` argument (``NA-CS-...`` for a consular case) — that is exactly the
case-type disclosure the ADR rules out, so the parameter is gone. If a caller
needs to know what kind of object a reference belongs to, it looks the
reference up and is authorised, which is the point.

Knowing a ``public_ref`` is a locator, not a credential (ADR-0007).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Final

from ulid import ULID

#: Crockford base-32. The four ambiguous glyphs ``I``, ``L``, ``O`` and ``U`` are
#: absent by construction, so a reference survives being read aloud down a phone
#: line and typed back by hand. ``U`` is excluded by Crockford specifically to
#: avoid accidental obscenities in generated tokens.
PUBLIC_REF_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 12 characters over a 32-symbol alphabet == exactly 60 bits of entropy,
#: the figure ADR-0007 argues for against enumeration.
PUBLIC_REF_TOKEN_LENGTH: Final[int] = 12

#: Split point for readability: ``NADDP-XXXXXXXX-XXXX``.
PUBLIC_REF_GROUP_SIZE: Final[int] = 8

PUBLIC_REF_NAMESPACE: Final[str] = "NADDP"

PUBLIC_REF_ENTROPY_BITS: Final[int] = 60

_ALPHABET_SIZE: Final[int] = len(PUBLIC_REF_ALPHABET)

#: Domain separation for :func:`seed_id`. Bumping it re-mints every seeded primary
#: key, which is a schema-drop-level change — do it only alongside ``make demo-reset``.
SEED_ID_NAMESPACE: Final[str] = "naddp.seed.ids.v1"

#: Millisecond epoch for a seeded id's synthetic ULID prefix. An arbitrary fixed
#: instant: the ULID timestamp of a seeded row records nothing real, and taking it
#: from the run clock would make every id move on every run.
_SEED_ID_EPOCH: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)
_SEED_ID_EPOCH_MS: Final[int] = int(_SEED_ID_EPOCH.timestamp() * 1000)

#: Width of the synthetic timestamp spread, in milliseconds. One day, so seeded ids
#: sort into a stable pseudo-random order rather than sharing one prefix.
_SEED_ID_SPREAD_MS: Final[int] = 86_400_000


def new_id() -> uuid.UUID:
    """Return a time-sortable ULID rendered as a UUID, for use as a primary key."""
    return ULID().to_uuid()


def seed_id(slug: str) -> uuid.UUID:
    """Return the deterministic ULID-shaped primary key for a seed ``slug``.

    A pure function: the same slug yields the same id on every machine and every
    run. That is what lets ``make seed`` upsert instead of duplicating, and what
    lets anything outside the seeder name a seeded row by slug.

    The layout is ADR-0007's -- six bytes of millisecond timestamp then ten bytes
    of entropy -- so the value is a well-formed ULID and ``ORDER BY id`` stays
    meaningful. The timestamp is synthetic and derived from the slug, because a
    seeded row has no honest creation instant to encode.

    Unrelated to :func:`new_public_ref` by construction, for the reasons above:
    this is derivable from a slug, so it must never be used as a public locator.
    """
    digest = hashlib.blake2b(f"{SEED_ID_NAMESPACE}:{slug}".encode(), digest_size=16).digest()
    moment = _SEED_ID_EPOCH_MS + int.from_bytes(digest[:4], "big") % _SEED_ID_SPREAD_MS
    return uuid.UUID(bytes=moment.to_bytes(6, "big") + digest[6:16])


def new_public_ref() -> str:
    """Return a fresh, opaque public reference such as ``NADDP-7F3K9QX2-4M2W``.

    Drawn from :func:`secrets.token_bytes` (CSPRNG), uniformly over the full
    2**60 space — no modulo bias, no timestamp, no context tag, and no shared
    entropy with any primary key.

    Uniqueness is enforced by a database constraint. Callers must retry on an
    integrity error rather than assuming the 60-bit space makes collision
    impossible.
    """
    # 8 bytes == 64 bits; discard the low 4 so the value is uniform over exactly
    # 2**60, which is 12 whole base-32 digits with nothing left over.
    value = int.from_bytes(secrets.token_bytes(8), "big") >> 4

    digits = []
    for _ in range(PUBLIC_REF_TOKEN_LENGTH):
        value, remainder = divmod(value, _ALPHABET_SIZE)
        digits.append(PUBLIC_REF_ALPHABET[remainder])
    token = "".join(reversed(digits))

    head = token[:PUBLIC_REF_GROUP_SIZE]
    tail = token[PUBLIC_REF_GROUP_SIZE:]
    return f"{PUBLIC_REF_NAMESPACE}-{head}-{tail}"


def is_public_ref(candidate: str) -> bool:
    """Return whether ``candidate`` is shaped like a public reference.

    A cheap syntactic guard for route parameters, so a malformed lookup is
    rejected before it reaches the database. It says nothing about whether the
    reference exists or whether the caller may see it.
    """
    parts = candidate.strip().upper().split("-")
    expected_tail = PUBLIC_REF_TOKEN_LENGTH - PUBLIC_REF_GROUP_SIZE
    if len(parts) != 3:
        return False
    namespace, head, tail = parts
    if namespace != PUBLIC_REF_NAMESPACE:
        return False
    if len(head) != PUBLIC_REF_GROUP_SIZE or len(tail) != expected_tail:
        return False
    return set(head + tail) <= set(PUBLIC_REF_ALPHABET)


def normalise_public_ref(candidate: str) -> str:
    """Return the canonical form of a public reference typed in by a human.

    Upper-cases and trims. Deliberately does NOT attempt to correct ambiguous
    glyphs (``O`` -> ``0``, ``I`` -> ``1``): those characters are absent from the
    alphabet, so a reference containing one was mistyped, and silently
    "fixing" it could map a typo onto a different, valid reference belonging to
    someone else.
    """
    return candidate.strip().upper()
