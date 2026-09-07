"""``make seed`` -- load the synthetic demo dataset.

Run as ``uv run --project apps/api python data/demo-seed/seed.py`` (see the ``seed`` target
in ``Makefile`` and ``make.ps1``, which are the same command).

WHAT THIS GUARANTEES
--------------------
**Idempotent.** Running it twice does not duplicate a row and does not crash. Every row is
keyed by a primary key that is a pure function of a stable slug, and written with an
explicit upsert; the two append-only tables (``audit_events``, ``case_events``) are
insert-once and are skipped when they are already populated. It is upsert-only, never
truncate-and-load, because ADR-0004's triggers and ``ON DELETE RESTRICT`` foreign keys make
a "clear everything" path unreachable from SQL. The clean-slate path is ``make demo-reset``.

**Deterministic.** One ``random.Random(FIXED_SEED)`` feeds every sampled value, so two runs
produce the same dataset and a rehearsal is repeatable. The single deliberate exception is
``cases.public_ref``, which ADR-0007 requires to come from a CSPRNG; it is minted once and
preserved across runs, and the values are written into ``manifest.json``.

**Anchored to now.** Every timestamp is ``now() - interval`` on the *database* clock. A
case seeded "four days old" is still four days old at a rehearsal three weeks later, so
``make demo-reset`` cannot age the dataset into an all-red dashboard.

**Loud on failure.** A citation that is not VERIFIED, a taxonomy code that does not exist,
a dangling object-store URI or a missed volume target stops the run with a non-zero exit
code. Every one of those is a defect that would otherwise be discovered on stage.

WHAT IT DOES NOT DO
-------------------
It does not delete rows an older version of the dataset created; ``make demo-reset`` is for
that. It does not compute embeddings -- Week 2 owns retrieval, and ``embedding`` is NULL
meaning "not embedded yet" rather than "no match".
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

# --------------------------------------------------------------------------------------
# Import bootstrap. `uv run --project apps/api python data/demo-seed/seed.py` puts THIS
# directory on sys.path and not `apps/api`, so `app` is not importable without help. The
# project is declared `package = false` (a deployed application, not a distributable), so
# there is no installed distribution to fall back on either. Both paths are added before
# any first-party import: this directory so `seed_parts` resolves when the script is run
# from elsewhere, and `apps/api` so `app` does.
# --------------------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
for _path in (_HERE, _REPO_ROOT / "apps" / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit.writer import verify_chain  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from seed_parts.audit import (  # noqa: E402
    AUDIT_ROW_TARGET,
    AUDIT_WINDOW_DAYS,
    seed_audit_history,
)
from seed_parts.briefs import seed_briefs  # noqa: E402
from seed_parts.consular import seed_cases  # noqa: E402
from seed_parts.context import FIXED_SEED, SeedContext  # noqa: E402
from seed_parts.diaspora import seed_diaspora  # noqa: E402
from seed_parts.governance import seed_governance  # noqa: E402
from seed_parts.knowledge import seed_knowledge  # noqa: E402
from seed_parts.meetings import seed_actions, seed_meetings  # noqa: E402
from seed_parts.opportunities import seed_opportunities  # noqa: E402
from seed_parts.signals import seed_signals  # noqa: E402
from seed_parts.sources import seed_sources_and_documents  # noqa: E402
from seed_parts.stakeholders import seed_interactions, seed_stakeholders  # noqa: E402
from seed_parts.targets import render, report  # noqa: E402
from seed_parts.traces import seed_traces  # noqa: E402

MANIFEST_PATH = _HERE / "manifest.json"


def _database_now(session: Session) -> datetime:
    """Read the transaction timestamp from the database, not from this host.

    The audit writer already insists on the database clock for the same reason: seeded
    rows, API rows and migration rows must share one time source, or "which happened
    first" depends on which machine wrote it.
    """
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        msg = "SELECT now() did not return a timestamp; refusing to seed against an unknown clock."
        raise RuntimeError(msg)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def main() -> int:
    """Load the dataset and print the volume report. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Load the NADDP synthetic demo dataset.")
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip the audit history block. For a fast iteration loop only.",
    )
    arguments = parser.parse_args()

    configure_logging(level="WARNING")

    with session_scope() as session:
        ctx = SeedContext(
            session=session,
            now=_database_now(session),
            # S311: not a security decision. This RNG shapes a demo dataset, and it is
            # seeded precisely BECAUSE it must be predictable -- a demo that looks
            # different after every reset cannot be rehearsed. The one value in the
            # dataset that must be unpredictable, cases.public_ref, comes from
            # app.core.ids.new_public_ref, which uses secrets (ADR-0007).
            rng=random.Random(FIXED_SEED),  # noqa: S311
        )

        print("NADDP demo seed - SYNTHETIC DATA ONLY")
        print(f"  run clock (database): {ctx.now.isoformat()}")
        print(f"  rng seed            : {FIXED_SEED}")
        print()

        print("  governance      ...", flush=True)
        users = seed_governance(ctx)

        print("  sources+documents...", flush=True)
        documents = seed_sources_and_documents(ctx)

        print("  ai traces       ...", flush=True)
        traces = seed_traces(ctx, users)

        print("  signals         ...", flush=True)
        signals = seed_signals(ctx, documents, users)

        print("  stakeholders    ...", flush=True)
        organisations, stakeholders = seed_stakeholders(ctx, users)
        seed_interactions(ctx, users, organisations, stakeholders)

        print("  opportunities   ...", flush=True)
        opportunities = seed_opportunities(ctx, users, organisations, stakeholders, signals, traces)

        print("  meetings        ...", flush=True)
        meetings = seed_meetings(ctx, users, organisations, stakeholders, opportunities, traces)

        print("  consular        ...", flush=True)
        cases = seed_cases(ctx, users)

        print("  actions         ...", flush=True)
        seed_actions(ctx, users, meetings, opportunities, cases)

        print("  diaspora        ...", flush=True)
        _tags, profiles = seed_diaspora(ctx)

        print("  knowledge       ...", flush=True)
        seed_knowledge(ctx, users, documents)

        print("  briefs          ...", flush=True)
        seed_briefs(ctx, documents, signals, opportunities, meetings, cases, traces)

        if arguments.skip_audit:
            print("  audit history   ... SKIPPED (--skip-audit)", flush=True)
        else:
            print(
                f"  audit history   ... {AUDIT_ROW_TARGET} rows over {AUDIT_WINDOW_DAYS} days",
                flush=True,
            )
            written = seed_audit_history(
                ctx,
                users,
                list(opportunities.values()),
                list(signals.values()),
                list(stakeholders.values()),
                list(cases.values()),
                list(profiles.values()),
            )
            if written == 0:
                print("                      (already seeded on an earlier run; skipped)")

        ctx.write_manifest(MANIFEST_PATH)
        print(f"\nmanifest written to {MANIFEST_PATH.relative_to(_REPO_ROOT)}")

        rows, ok = report(ctx, AUDIT_ROW_TARGET, AUDIT_WINDOW_DAYS)
        print("\n" + render(rows))

        verification = verify_chain(session)
        print(
            f"\naudit hash chain: {'intact' if verification.is_intact else 'BROKEN'} "
            f"({verification.checked} rows verified)"
        )
        if not verification.is_intact:
            print(f"  broken at {verification.broken_at_id}: {verification.reason}")
            ok = False

    if not ok:
        print("\nSEED FAILED: a volume target was missed or the audit chain did not verify.")
        return 1
    print("\nseed complete - every target met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
