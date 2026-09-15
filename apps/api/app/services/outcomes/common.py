"""The shapes every outcomes context returns, and the one authorisation primitive they share.

Nothing here queries. Each context module under ``app.services.outcomes`` owns its statements,
against its own tables, behind its own :func:`authorise` call; this module gives those answers a
common shape so the board can lay them side by side without pooling them.

**A withheld figure is not a zero.** When a gate refuses, the section carries no figures and the
step carries no facts -- ``value`` is ``None``, never ``0`` -- because "you may not see this" and
"there is nothing to see" are different facts (the command centre's contract, ADR-0003).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.domain.enums import Classification
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "NOT_FOUND_NOTE",
    "Gate",
    "OutcomeFigure",
    "OutcomeSection",
    "ThreadFact",
    "ThreadStep",
    "Tone",
    "authorise",
    "count",
    "days_ago",
    "humanise",
    "section_tone",
    "unfound_step",
    "withheld_section",
    "withheld_step",
]

#: State a figure or step reports, in the Mission Slate semantic vocabulary.
Tone = Literal["neutral", "ok", "warn", "risk", "proposed"]

#: For an anchor the caller may read about but cannot find. Worded so that "absent" and "outside
#: your clearance" read identically: telling those two apart would disclose the record.
NOT_FOUND_NOTE: Final[str] = "Not found in the records you are cleared to read."


@dataclass(frozen=True, slots=True)
class Gate:
    """One authorisation decision, taken by the context that owns the figure."""

    required: tuple[Permission, ...]
    missing: tuple[Permission, ...]

    @property
    def granted(self) -> bool:
        return not self.missing


def authorise(principal: Principal, *required: Permission) -> Gate:
    """All-of: the gate opens only when ``principal`` holds every permission in ``required``."""
    return Gate(
        required=tuple(required),
        missing=tuple(permission for permission in required if not principal.has(permission)),
    )


@dataclass(frozen=True, slots=True)
class OutcomeFigure:
    """One outcome. ``value`` is ``None`` only when this figure's own gate refused."""

    key: str
    label: str
    gate: Gate
    value: int | None
    detail: str
    #: A denominator, for "4 of 5" figures.
    of: int | None = None
    tone: Tone = "neutral"


@dataclass(frozen=True, slots=True)
class OutcomeSection:
    """One bounded context's outcomes. Empty ``figures`` and zones when its gate refused."""

    key: str
    title: str
    bounded_context: str
    summary: str
    gate: Gate
    counted_across: tuple[Classification, ...]
    figures: tuple[OutcomeFigure, ...]
    href: str | None = None


def section_tone(figures: Sequence[OutcomeFigure]) -> Tone:
    """The state tick a section carries: risk or warning only. Resolved and proposed are not ticks.

    Decided here so the web renders a tick it was given, rather than deriving one from figures.
    """
    tones = {figure.tone for figure in figures}
    if "risk" in tones:
        return "risk"
    if "warn" in tones:
        return "warn"
    return "neutral"


def withheld_section(
    *, key: str, title: str, bounded_context: str, summary: str, gate: Gate
) -> OutcomeSection:
    """A section whose gate refused. Nothing was queried, so nothing is reported."""
    return OutcomeSection(
        key=key,
        title=title,
        bounded_context=bounded_context,
        summary=summary,
        gate=gate,
        counted_across=(),
        figures=(),
    )


@dataclass(frozen=True, slots=True)
class ThreadFact:
    """One labelled fact on a thread step, worded by the server."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class ThreadStep:
    """One context's place on the hero thread.

    ``found`` is ``None`` when the gate refused (the question was never asked), ``False`` when the
    anchor is not among the records the caller may read, and ``True`` otherwise.
    """

    key: str
    title: str
    bounded_context: str
    gate: Gate
    found: bool | None
    headline: str | None = None
    facts: tuple[ThreadFact, ...] = ()
    note: str | None = None
    tone: Tone = "neutral"
    trace_id: uuid.UUID | None = None
    href: str | None = None


def withheld_step(*, key: str, title: str, bounded_context: str, gate: Gate) -> ThreadStep:
    """A step whose gate refused."""
    return ThreadStep(key=key, title=title, bounded_context=bounded_context, gate=gate, found=None)


def unfound_step(*, key: str, title: str, bounded_context: str, gate: Gate) -> ThreadStep:
    """A step the caller may read, whose anchor is not among the records they are cleared for."""
    return ThreadStep(
        key=key,
        title=title,
        bounded_context=bounded_context,
        gate=gate,
        found=False,
        note=NOT_FOUND_NOTE,
    )


def count(session: Session, statement: Select[tuple[int]]) -> int:
    """Run a ``count()`` statement; an empty table is 0."""
    return int(session.scalar(statement) or 0)


def humanise(code: str) -> str:
    """``CONTACT_PLANNED`` to "Contact planned"."""
    return code.replace("_", " ").capitalize()


def days_ago(moment: datetime, now: datetime) -> str:
    """A relative day phrase: today, 1 day ago, 6 days ago, or in 3 days for a future moment."""
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        ahead = math.ceil(-seconds / 86_400)
        return "in 1 day" if ahead == 1 else f"in {ahead} days"
    days = int(seconds // 86_400)
    if days == 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"
