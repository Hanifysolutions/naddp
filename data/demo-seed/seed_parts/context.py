"""Shared plumbing: determinism, identity, idempotent writes, and the run manifest.

Four decisions live here and are made once for the whole seed.

**1. Determinism.** Every random choice in the dataset comes from :attr:`SeedContext.rng`,
a ``random.Random`` bound to :data:`FIXED_SEED`. Module-level ``random`` is never used: a
demo that looks different after every ``make demo-reset`` cannot be rehearsed, and a
screenshot taken on Tuesday has to still match the screen on Thursday.

**2. Identity.** Primary keys are minted by :func:`mint_id` as a pure function of a stable
slug, rendered into the ULID layout ADR-0007 specifies (48-bit millisecond prefix, 80 bits
of entropy). Pure-function-of-slug rather than a running counter, because a counter makes
every id downstream of an inserted row move -- and moving ids turn an upsert into a
duplicate.

**3. Idempotency.** ``make seed`` is **upsert-only**, never truncate-and-load. That is not
a preference: ``audit_events`` and ``case_events`` refuse ``DELETE`` and ``TRUNCATE``
(ADR-0004 triggers), and the ``ON DELETE RESTRICT`` foreign keys from ``audit_events`` to
``users`` and from ``case_events`` to ``cases`` mean a "clear everything" path is not
reachable from SQL at all. The clean-slate path is ``make demo-reset``, which drops and
recreates the schema. Consequence, stated plainly: a row that an *older* seed version
created and this one no longer describes is left behind by ``make seed``. Run
``make demo-reset`` after changing the dataset.

**4. Time.** Every timestamp is derived from :attr:`SeedContext.now`, read once from the
**database** clock, via :meth:`SeedContext.days_ago` and friends. Nothing is hard-coded to
a calendar date, so a case seeded "four days old" is still four days old at a rehearsal
three weeks later (Q-13, and ``data/demo-seed/README.md`` section 6).
"""

from __future__ import annotations

import json
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ulid import ULID

from app.core.ids import SEED_ID_NAMESPACE, seed_id

__all__ = [
    "FIXED_SEED",
    "ID_NAMESPACE",
    "SEED_MARKER",
    "SeedContext",
    "mint_id",
    "ulid_str",
    "weighted_recent_offsets",
]

#: The one random seed. Changing it changes every sampled value in the dataset.
FIXED_SEED: Final[int] = 20260907

#: Domain separation for :func:`mint_id`. Defined in ``app.core.ids`` because the running
#: API needs the same answer to find a seeded row again, and re-exported here under the
#: name the seed has always used. Bumping it re-mints every primary key, which is a
#: schema-drop-level change -- do it only alongside ``make demo-reset``.
ID_NAMESPACE: Final[str] = SEED_ID_NAMESPACE

#: Written into ``audit_events.payload.seed_marker`` so seeded history is distinguishable
#: from rows the running API appended. The seed counts and skips on this, which is what
#: makes the append-only audit block re-runnable.
SEED_MARKER: Final[str] = "naddp-demo-seed-v1"

T = TypeVar("T")


def mint_id(slug: str) -> uuid.UUID:
    """Return the deterministic ULID-shaped primary key for ``slug``.

    A pure function: the same slug yields the same id on every machine and every run,
    which is what lets ``make seed`` upsert instead of duplicating, and what lets anything
    else name a seeded row by slug rather than by an id that moves.

    Delegates to :func:`app.core.ids.seed_id`, which is where the implementation now lives.
    The seeder is no longer its only caller: ``app.services.outcomes.anchors`` resolves the
    hero thread's slugs the same way, at request time, and the two must not be able to
    disagree about the row the seeder wrote.
    """
    return seed_id(slug)


def ulid_str(value: uuid.UUID) -> str:
    """Render a UUID primary key back into its 26-character Crockford ULID form.

    Used for object-store paths: ``storage/README.md`` specifies
    ``file://storage/<context>/<ulid>/<filename>``, and the ULID -- not the UUID -- is what
    it means.
    """
    return str(ULID.from_uuid(value))


def weighted_recent_offsets(
    rng: random.Random,
    count: int,
    span_days: int,
    *,
    bias: float,
) -> list[float]:
    """Return ``count`` day-offsets in ``[0, span_days)``, weighted toward zero.

    ``bias`` above 1 pulls the distribution toward the present: an offset is
    ``span_days * u ** bias`` for uniform ``u``, so ``bias = 2.2`` puts roughly half the
    rows in the most recent fifth of the window. Q-13 asks for a trailing eight weeks
    "weighted toward recent", and a flat sample looks like a generator rather than a log.
    """
    return sorted(span_days * rng.random() ** bias for _ in range(count))


@dataclass(slots=True)
class SeedContext:
    """Everything a seed module needs, and nothing it does not.

    Threaded through every module rather than stashed in globals, so a module cannot
    quietly acquire a second clock or a second random source.
    """

    session: Session
    now: datetime
    rng: random.Random
    manifest: dict[str, dict[str, str]] = field(default_factory=dict)
    slugs_seen: set[str] = field(default_factory=set)

    # -- time ---------------------------------------------------------------

    def days_ago(self, days: float, *, hour: int | None = None) -> datetime:
        """A moment ``days`` before the run, optionally pinned to a wall-clock hour."""
        moment = self.now - timedelta(days=days)
        if hour is not None:
            moment = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
        return moment

    def days_ahead(self, days: float, *, hour: int | None = None) -> datetime:
        """A moment ``days`` after the run, optionally pinned to a wall-clock hour."""
        return self.days_ago(-days, hour=hour)

    def hours_ago(self, hours: float) -> datetime:
        """A moment ``hours`` before the run."""
        return self.now - timedelta(hours=hours)

    def hours_ahead(self, hours: float) -> datetime:
        """A moment ``hours`` after the run."""
        return self.now + timedelta(hours=hours)

    @property
    def today(self) -> date:
        """The run's calendar date, on the database clock."""
        return self.now.date()

    # -- identity and the manifest -----------------------------------------

    def register(self, kind: str, slug: str) -> uuid.UUID:
        """Mint the id for ``slug`` and record it in the run manifest under ``kind``.

        Raises:
            ValueError: if ``slug`` has already been registered. Two rows sharing a slug
                would share a primary key, and the second would silently overwrite the
                first -- a bug that presents as "half the dataset is missing".
        """
        if slug in self.slugs_seen:
            msg = f"duplicate seed slug {slug!r}: every seeded row needs a unique slug."
            raise ValueError(msg)
        self.slugs_seen.add(slug)
        identifier = mint_id(slug)
        self.manifest.setdefault(kind, {})[slug] = str(identifier)
        return identifier

    def note(self, kind: str, slug: str, value: str) -> None:
        """Record a non-id fact in the manifest, e.g. a generated ``public_ref``."""
        self.manifest.setdefault(kind, {})[slug] = value

    # -- idempotent writes --------------------------------------------------

    def upsert(self, model: type[T], primary_key: Any, /, **fields: Any) -> T:  # noqa: ANN401
        """Insert ``model`` at ``primary_key``, or update the existing row in place.

        Deliberately explicit rather than ``Session.merge``: merge decides for itself what
        an unset attribute means, and the tables here mix Python-side defaults,
        server-side defaults and columns the seed must never touch (``created_at``). An
        explicit ``setattr`` loop only ever writes what the caller named.

        ``primary_key`` is a UUID, or a mapping for the composite-key association tables.
        """
        existing: Any = self.session.get(model, primary_key)
        if existing is None:
            keys = dict(primary_key) if isinstance(primary_key, dict) else {"id": primary_key}
            created: Any = model(**keys, **fields)
            self.session.add(created)
            return created  # type: ignore[no-any-return]
        for name, value in fields.items():
            setattr(existing, name, value)
        return existing  # type: ignore[no-any-return]

    def insert_once(self, model: type[T], primary_key: Any, /, **fields: Any) -> T | None:  # noqa: ANN401
        """Insert ``model`` at ``primary_key`` only if it is absent; never update.

        For the append-only tables. ``case_events`` refuses ``UPDATE`` at the database
        trigger, so :meth:`upsert`'s ``setattr`` loop would raise on the second run even
        when it is writing byte-identical values.
        """
        if self.session.get(model, primary_key) is not None:
            return None
        keys = dict(primary_key) if isinstance(primary_key, dict) else {"id": primary_key}
        created: Any = model(**keys, **fields)
        self.session.add(created)
        return created  # type: ignore[no-any-return]

    # -- counting -----------------------------------------------------------

    def count(self, model: type[Any]) -> int:
        """Return the row count of ``model``'s table, flushing pending work first."""
        self.session.flush()
        return int(self.session.scalar(select(func.count()).select_from(model)) or 0)

    def pick(self, values: Sequence[T]) -> T:
        """Deterministically choose one of ``values``."""
        return values[self.rng.randrange(len(values))]

    def write_manifest(self, path: Path) -> None:
        """Write ``manifest.json``: stable slug to current-run identifier.

        ``data/demo-seed/README.md`` requires this so an eval case, a fallback snapshot or
        a demo script can name ``opp-au-lithium-ng-skills-corridor`` rather than an id
        that moves with the dataset.
        """
        entries = {kind: dict(sorted(rows.items())) for kind, rows in sorted(self.manifest.items())}
        document: dict[str, Any] = {
            "$schema_version": "1.0",
            "generated_by": "data/demo-seed/seed.py",
            "seed_marker": SEED_MARKER,
            "id_namespace": ID_NAMESPACE,
            "note": (
                "Identifiers are a pure function of the slug (see seed_parts/context.py), "
                "so they are stable across runs and across machines. Values under "
                "'case_public_ref' are NOT: cases.public_ref is minted by "
                "app.core.ids.new_public_ref (CSPRNG, ADR-0007) and is read back from the "
                "database on every run."
            ),
            "entries": entries,
        }
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
