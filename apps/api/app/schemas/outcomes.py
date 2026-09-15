"""Pydantic v2 wire shapes for ``GET /v1/outcomes``: the Unified Outcomes board.

Every section, figure and thread step carries its own ``authorisation`` -- the permissions its
bounded context required and which of them the caller lacked -- because the board's claim is that
each figure was counted in its own domain under its own check. A withheld section has no figures
and no zones; a withheld figure has ``value: null``. Neither is ever a zero.

Everything a screen shows is here, worded and counted by the server. The web formats numbers and
lays out; it computes nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Classification

__all__ = [
    "AuthorisationResponse",
    "HeroThreadResponse",
    "OutcomeFigureResponse",
    "OutcomeSectionResponse",
    "OutcomesBoardResponse",
    "ThreadFactResponse",
    "ThreadStepResponse",
]

Tone = Literal["neutral", "ok", "warn", "risk", "proposed"]


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorisationResponse(_Shape):
    """The check one section, figure or step was counted under."""

    required_permissions: list[str] = Field(description="All of these were required.")
    missing_permissions: list[str] = Field(description="Which of them the caller does not hold.")
    granted: bool = Field(description="True when nothing is missing.")


class OutcomeFigureResponse(_Shape):
    """One outcome figure."""

    key: str
    label: str
    value: int | None = Field(
        description="Null only when this figure's own authorisation was refused. Never a "
        "stand-in zero."
    )
    of: int | None = Field(default=None, description="Denominator, for an 'N of M' figure.")
    detail: str = Field(description="What the figure counts, in one sentence.")
    tone: Tone = Field(description="State, decided by the server.")
    authorisation: AuthorisationResponse


class OutcomeSectionResponse(_Shape):
    """One bounded context's outcomes."""

    key: Literal["bilateral", "citizen_service", "diaspora", "relationships", "meetings"]
    title: str
    bounded_context: str = Field(description="The context that counted these figures.")
    summary: str
    authorisation: AuthorisationResponse
    tone: Tone = Field(description="The section's state tick: risk, warn or neutral.")
    counted_across: list[Classification] = Field(
        description="The zones every figure here was counted across. Empty when withheld."
    )
    figures: list[OutcomeFigureResponse] = Field(
        description="Empty when the section's authorisation was refused: nothing was queried."
    )
    href: str | None = Field(default=None, description="The screen these figures summarise.")


class ThreadFactResponse(_Shape):
    """One labelled fact on a thread step."""

    label: str
    value: str


class ThreadStepResponse(_Shape):
    """One context's place on the hero thread."""

    key: Literal["opportunity", "stakeholder", "meeting", "diaspora", "consular"]
    title: str
    bounded_context: str
    authorisation: AuthorisationResponse
    found: bool | None = Field(
        description="Null when withheld (never asked); false when not among the records you are "
        "cleared to read; true otherwise."
    )
    headline: str | None = None
    facts: list[ThreadFactResponse]
    note: str | None = None
    tone: Tone
    trace_id: uuid.UUID | None = Field(
        default=None,
        description="The AI trace behind this step, when an AI call produced it (section 4a).",
    )
    href: str | None = None


class HeroThreadResponse(_Shape):
    """The corridor, followed across contexts, each step governed separately."""

    id: str
    title: str
    steps: list[ThreadStepResponse]


class OutcomesBoardResponse(_Shape):
    """The Unified Outcomes board for the calling principal."""

    generated_at: datetime
    readable_classifications: list[Classification] = Field(
        description="The zones your clearance admits. Each section applies them itself."
    )
    domains_readable: int = Field(description="Sections whose authorisation was granted.")
    domains_total: int = Field(description="Sections on the board.")
    sections: list[OutcomeSectionResponse]
    thread: HeroThreadResponse
