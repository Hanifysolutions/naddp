"""The SQLAlchemy 2.0 declarative base for every table in the system.

Three things live here, and nothing else:

1. :data:`NAMING_CONVENTION` -- deterministic constraint and index names.
2. :class:`Base` -- the declarative base, carrying the metadata and the
   ``type_annotation_map`` that turns a Python annotation into a Postgres column type.
3. :func:`pg_enum` -- the single way a native Postgres enum is declared, so all 26 tables
   declare theirs identically.

At the very bottom, every model module is imported. That import block is what makes
``import app.models.base`` sufficient to register all 26 tables on ``Base.metadata``,
which is precisely what ``alembic/env.py`` relies on when it reads ``target_metadata``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Final

from sqlalchemy import DateTime, MetaData, Numeric, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import DeclarativeBase

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "pg_enum",
]

# ---------------------------------------------------------------------------
# Naming convention
# ---------------------------------------------------------------------------
#
# This is NOT optional and NOT cosmetic.
#
# Postgres invents a name for any constraint or index created without one
# (``opportunities_stakeholder_id_fkey``, ``organisations_name_key``, ...). Alembic
# autogenerate then emits ``op.create_foreign_key(None, ...)`` and, crucially, cannot emit
# a ``drop_constraint`` for it later, because a drop needs a name and there is none in the
# model metadata to render. The result is a migration chain that can add constraints but
# never remove or alter them, which is discovered at exactly the wrong moment.
#
# Fixing this on day one costs one dict. Retrofitting it means a migration that renames
# every constraint in the database first.
#
# ``column_0_N_name`` (rather than ``column_0_name``) includes *every* column of a
# composite constraint. Two different composite unique constraints on the same table would
# otherwise generate the same name and collide -- ``role_permissions`` and
# ``meeting_attendees`` are exactly that shape.
#
# Caveat for the model modules: Postgres truncates identifiers at 63 bytes. A very long
# table name plus a very long column name plus a long referenced table name can exceed it,
# and two constraints that differ only past byte 63 would then collide. If you write a
# table whose generated FK name approaches that limit, pass an explicit ``name=`` rather
# than letting the convention run long.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all NADDP ORM models.

    ``type_annotation_map`` is the reason a model can write
    ``created_at: Mapped[datetime]`` and get a ``TIMESTAMP WITH TIME ZONE`` rather than a
    naive ``TIMESTAMP``. Every mapping below is a locked decision, applied once here so
    that 26 tables cannot each get it subtly wrong:

    ``uuid.UUID`` -> ``UUID(as_uuid=True)``
        Native 16-byte Postgres ``uuid``. Primary keys are ULIDs *rendered* into that
        type (ADR-0007): 128 bits either way, so this is a rendering choice and not a
        conversion, and every UUID-aware driver and tool keeps working.

    ``datetime`` -> ``DateTime(timezone=True)``
        ``timestamptz``, always. A naive timestamp in a system that spans Abuja and
        Canberra is a bug waiting for a demo audience.

    ``dict[str, Any]`` and ``list[str]`` -> ``JSONB``
        Binary JSON, so it is indexable and queryable -- ``audit_events.detail`` is read
        by the trace drawer, not merely stored.

    ``str`` -> ``Text``
        Postgres stores ``text`` and ``varchar(n)`` identically and neither is faster.
        An unbounded default means no column is silently truncated. A column that genuinely
        needs a bound states it explicitly with ``String(n)``, and that bound then reads as
        a deliberate domain constraint rather than an accident of the default.

    ``Decimal`` -> ``Numeric``
        Money is ``Numeric(18, 2)`` and scores are ``Numeric(5, 2)``, declared per column.
        Never float: binary floating point cannot represent 0.10, and a demo that shows a
        rounding artefact in a trade figure has lost the room.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # ``ClassVar`` is required: SQLAlchemy declares this attribute as a class variable, and
    # without the annotation the declarative machinery would try to map it as a column.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: PgUUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        str: Text,
        Decimal: Numeric,
    }


def pg_enum[E: enum.Enum](enum_cls: type[E], name: str) -> SAEnum:
    """Declare a native Postgres enum type for ``enum_cls`` under the type name ``name``.

    Every enum column in all 26 tables goes through this function, so that the DDL, the
    ORM and the wire format cannot drift apart table by table.

    **Why ``values_callable`` is not optional.** By default SQLAlchemy persists an enum by
    its member *name*, not its *value*. For a straightforward enum those strings coincide
    and nothing appears wrong -- until one does not. The API, the seed loader and the web
    client all speak ``Classification.value``, so without ``values_callable`` the database
    would silently hold a different vocabulary from everything that reads and writes it,
    and the failure would surface as an ``invalid input value for enum`` on a value that
    looks perfectly correct in the Python source. Passing ``values_callable`` makes the
    Postgres labels literally ``[m.value for m in enum_cls]``, so one string means one
    thing from the browser through to the DDL.

    ``native_enum=True`` gets a real Postgres ``CREATE TYPE`` rather than a ``VARCHAR``
    plus a check constraint: the database rejects an unknown value regardless of which
    client wrote it, which is what makes a classification zone a constraint rather than a
    convention (ADR-0006 requires exactly four values, changeable only by migration).

    ``create_type=True`` lets the type be created alongside the first table that uses it.
    Where one enum type is shared by several tables -- ``classification`` is, via
    ``ClassifiedMixin`` -- share a *single* instance of the returned object across those
    columns rather than calling this function once per table; SQLAlchemy then emits one
    ``CREATE TYPE``. See ``app.models.mixins.CLASSIFICATION_ENUM``.

    Args:
        enum_cls: The Python enum. In this codebase always a ``(str, Enum)`` from
            ``app.domain.enums``.
        name: The Postgres type name, ``snake_case`` and singular, e.g. ``classification``,
            ``case_status``, ``opportunity_stage``. It is global to the database schema,
            so it must be unique across all 26 tables.

    Returns:
        A configured :class:`sqlalchemy.Enum` ready to pass to ``mapped_column``.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=True,
        values_callable=lambda e: [str(member.value) for member in e],
    )


# ---------------------------------------------------------------------------
# Model registration -- MUST stay at the bottom of this file
# ---------------------------------------------------------------------------
#
# These imports exist for their side effect: importing a model module runs its class
# definitions, and each ``Base`` subclass registers its table on ``Base.metadata``.
# ``alembic/env.py`` imports only ``app.models.base`` and reads ``Base.metadata``, so a
# module missing from this list is a table missing from every autogenerated migration --
# silently, with no error, which is the worst possible failure mode for a schema tool.
#
# They sit at the BOTTOM, after ``Base`` and ``pg_enum`` are defined, because the direction
# of dependency is the other way round: every model module does
# ``from app.models.base import Base``. Placing these imports at the top would mean
# ``base`` importing ``governance`` before ``Base`` exists, and ``governance`` would fail on
# a partially-initialised module. At the bottom, ``Base`` is already bound by the time any
# sibling asks for it, so the cycle resolves. (Importing a sibling first is also safe:
# Python re-enters ``base``, finishes it -- including this block, which finds the
# in-progress sibling already in ``sys.modules`` -- and returns.)
#
# The ``import app.models.x`` form is deliberate, and ``from app.models import x`` is NOT
# equivalent here even though both have the same registration side effect. The ``from``
# form asks the *package object* for an attribute that only exists once the submodule has
# finished importing, which is exactly what a cycle cannot guarantee. It works at runtime
# (Python 3.7+ falls back to ``sys.modules``) but mypy rejects all nine as
# ``Module "app.models" has no attribute ...`` under this cycle. The plain ``import`` form
# type-checks clean and never depends on that fallback.
#
# ``E402`` is module-level-import-not-at-top-of-file: correct in general, and precisely
# what this block must violate. ``F401`` (imported-but-unused) is needed on the LAST
# statement only: all nine bind the same single name ``app``, so only the final one
# leaves an unused binding for ruff to see.

import app.models.ai  # noqa: E402
import app.models.consular  # noqa: E402
import app.models.diaspora  # noqa: E402
import app.models.governance  # noqa: E402
import app.models.intelligence  # noqa: E402
import app.models.knowledge  # noqa: E402
import app.models.meetings  # noqa: E402
import app.models.opportunities  # noqa: E402
import app.models.retrieval  # noqa: E402
import app.models.stakeholders  # noqa: E402, F401
