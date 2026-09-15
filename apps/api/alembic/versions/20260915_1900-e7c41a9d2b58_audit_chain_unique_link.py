"""audit chain unique link

Revision ID: e7c41a9d2b58
Revises: bd6ba29a9bde
Create Date: 2026-09-15 19:00:00.000000+00:00

HAND-WRITTEN. Closes ``docs/W1_STATUS.md`` section 6 item 1: the audit hash chain could fork
under concurrent writes.

WHAT THIS REVISION DOES (W4.3)
------------------------------
``uq_audit_events_prev_event_hash``: a UNIQUE index on ``audit_events.prev_event_hash``,
``NULLS NOT DISTINCT``. No two rows may claim the same predecessor, and -- because the genesis
row's NULL counts as a value -- only one row may be the genesis. Two writers that read the same
head can no longer both link to it: the second insert is refused by the database, and
``app.audit.writer`` rolls back to a savepoint, re-reads the head and re-links. The chain is
linear by construction rather than by a lock that gave up after a budget.

Postgres 15+ (``NULLS NOT DISTINCT``); the stack runs 16.

WHAT IT REFUSES TO DO
---------------------
It does not repair a chain that already forks. ``audit_events`` is append-only (ADR-0004):
there is no sanctioned way to remove or relink an existing row, and the migration must not be
the exception. If a fork is present the upgrade stops and says so; on the demo stack the answer
is ``make demo-reset``, which rebuilds the log through the writer.

The append-only triggers, the privileges and the ORM guard are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c41a9d2b58"
down_revision: str | None = "bd6ba29a9bde"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINK_INDEX: Final[str] = "uq_audit_events_prev_event_hash"


def upgrade() -> None:
    claimed_twice = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ("
                "  SELECT prev_event_hash FROM audit_events"
                "  GROUP BY prev_event_hash HAVING count(*) > 1"
                ") AS forks"
            )
        )
        .scalar_one()
    )
    if claimed_twice:
        msg = (
            f"audit_events already forks at {claimed_twice} link(s): more than one row claims "
            "the same predecessor. An append-only log cannot be relinked in place, so "
            f"{LINK_INDEX} cannot be created. On the demo stack run `make demo-reset`, which "
            "rebuilds the log through the writer."
        )
        raise RuntimeError(msg)

    op.execute(
        f"CREATE UNIQUE INDEX {LINK_INDEX} ON audit_events (prev_event_hash) NULLS NOT DISTINCT"
    )
    op.execute(
        f"COMMENT ON INDEX {LINK_INDEX} IS "
        "'The audit chain cannot fork: no two rows may claim the same predecessor, and NULLS "
        "NOT DISTINCT allows one genesis row. app.audit.writer re-links a writer that lost the "
        "race rather than writing an unlinked row (docs/W1_STATUS.md section 6 item 1).'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {LINK_INDEX}")
