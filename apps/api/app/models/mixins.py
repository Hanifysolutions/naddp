"""Reusable declarative mixins.

Three concerns that recur across the 26 tables, factored out so each is decided once:
identity (:class:`UUIDPrimaryKeyMixin`), lifecycle timestamps (:class:`TimestampMixin`)
and data classification (:class:`ClassifiedMixin`).

These are plain classes, not mapped ones. SQLAlchemy 2.0 copies a mixin's
``mapped_column`` onto each concrete subclass at mapper configuration time, so a mixin
contributes columns without creating a table or an inheritance hierarchy of its own.

Usage::

    class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
        __tablename__ = "opportunities"

Put ``Base`` last: the mixins must appear earlier in the MRO so their columns are picked
up before the declarative machinery finalises the mapping.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column

from app.core.ids import new_id
from app.domain.enums import Classification
from app.models.base import pg_enum

__all__ = [
    "CLASSIFICATION_ENUM",
    "ClassifiedMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]

#: The one shared ``classification`` Postgres enum type.
#:
#: Declared once at module level rather than per table. Postgres enum types are global to
#: the schema, so ten tables each constructing their own ``pg_enum(Classification,
#: "classification")`` would be ten SQLAlchemy objects competing to ``CREATE TYPE
#: classification``, and Alembic would have to be told which one owns the DDL. One shared
#: instance means one type, created once, referenced everywhere.
#:
#: Exported so a model that needs a *second* classification column on the same row (an
#: original zone alongside a reclassified one, say) reuses this instance instead of
#: minting a rival type.
CLASSIFICATION_ENUM: Final[SAEnum] = pg_enum(Classification, "classification")


@declarative_mixin
class UUIDPrimaryKeyMixin:
    """A ULID-in-UUID primary key, generated in Python (ADR-0007).

    ``default=new_id`` is a *Python-side* column default, deliberately. SQLAlchemy calls
    ``new_id()`` in the application process during flush, immediately before the INSERT is
    emitted -- never in the database. That is what lets the seed script build a whole
    object graph (signal to opportunity to stakeholder to meeting to case), relate it in
    memory, and flush it in one transaction: SQLAlchemy resolves the parent IDs and the
    dependent foreign keys itself, in dependency order, without a round trip per row to
    learn what a server-side ``gen_random_uuid()`` decided.

    **Know what this does not do.** A column default is evaluated at flush, not at
    ``__init__``, so ``Opportunity(...).id`` is ``None`` until the object is flushed. Relate
    objects through relationships (``opportunity.signal = signal``), not by copying
    ``signal.id`` into a foreign key before flush -- the latter reads ``None`` and fails a
    NOT NULL constraint. Code that genuinely needs the identifier earlier (a deterministic
    fixture, a test asserting on an ID it minted) assigns it explicitly with
    ``obj.id = new_id()``, which the default then leaves alone. ADR-0007's "IDs are known
    before flush" holds for the object-graph use it argues for; it is not a promise that
    the attribute is populated at construction.

    A server-side default would forfeit even that: the ID would exist only after the INSERT
    returned.

    The column type comes from ``Base.type_annotation_map``: ``uuid.UUID`` maps to a native
    Postgres ``uuid``, so a ULID is stored in 16 bytes rather than 26 characters.

    Because a ULID's leading 48 bits are a millisecond timestamp, ``ORDER BY id`` is a
    valid chronological order -- which is what ADR-0004 relies on to give ``audit_events``
    a total order that does not depend on trusting a separate clock column. The same
    property is why an internal ID must never appear on an unauthenticated surface: it
    discloses its own creation time. Citizen-facing references are a separate, independent,
    random column (``new_public_ref``).
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)


@declarative_mixin
class TimestampMixin:
    """Creation and modification timestamps, both ``timestamptz``, both set by the database.

    ``server_default=func.now()`` rather than a Python default: the timestamp is the
    database server's clock, so rows written by the API, the seed script and a migration
    all share one time source. A clock skew between an application host and the database
    would otherwise make the ordering of two rows depend on which process wrote them.

    ``updated_at`` carries ``onupdate=func.now()`` so it is refreshed on every UPDATE
    issued through the ORM, and ``server_default`` so it is populated on INSERT too --
    a freshly created row has ``updated_at == created_at`` rather than NULL, which keeps
    "sort by last touched" honest without a ``COALESCE``.

    Note what this mixin is *not*: it is not an audit trail. It says when a row last
    changed, never who changed it or why. That is ``audit_events`` (ADR-0004), and
    ``audit_events`` itself must not use this mixin -- an append-only table has no
    ``updated_at``, and offering one would invite code that tries to use it.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@declarative_mixin
class ClassifiedMixin:
    """A ``classification`` zone on any table that can hold non-public content (ADR-0006).

    ``default=Classification.MISSION_INTERNAL`` is the safe default, and the choice is the
    whole point of the mixin. Authorisation in this system is deny-by-default, so a row
    that arrives without an explicit zone must not be world-readable. ``PUBLIC`` as a
    default would mean that every future ingest path, seed fixture or model that simply
    forgets to set the column leaks by omission -- and an omission is invisible in review.
    ``MISSION_INTERNAL`` fails closed instead: the row is over-classified, someone notices
    because they cannot read it, and over-classification is a nuisance where the opposite
    is an incident. ADR-0006 point 6 states this directly ("default on ingest is not
    ``PUBLIC``").

    Anything ingested into a consular context defaults to ``CONSULAR_SENSITIVE`` instead;
    that is a stricter default set by the consular models and services, never a relaxation
    of this one. Nothing computes its way *down* the lattice: a downgrade is a deliberate
    human act writing an ``object.reclassified`` audit row.

    The column is NOT NULL with no server default. A NULL zone would be a third, unhandled
    state that every ``may_read`` predicate would have to special-case, and one that
    forgets to is a leak; the Python-side default guarantees a value on every ORM insert.
    The server default is deliberately omitted so that a raw ``INSERT`` bypassing the ORM
    fails loudly rather than acquiring a zone nobody chose.

    The type is the shared :data:`CLASSIFICATION_ENUM`, so every classified table points at
    one Postgres enum type carrying exactly the four ADR-0006 values.
    """

    classification: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.MISSION_INTERNAL,
    )
