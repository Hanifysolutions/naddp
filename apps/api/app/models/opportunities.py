"""Opportunities -- the trade pipeline, and the spine of the demo narrative.

One table, ``opportunities``. It is small in column count and large in consequence: it is
the row the Ambassador looks at, the row winning moment #3 ("one governed picture") joins
everything else to, and the row winning moment #2 ("AI drafts, humans decide") is argued
from -- because an opportunity can be a thing the platform *proposed* rather than a thing
it *reported*.

Binding sources:

* ``BUILD_BIBLE.md`` sections 2 (hero thread) and 9 (state machine)
* ``docs/workflows.md`` section 1 -- the opportunity state machine, transcribed into
  ``app.domain.enums.OPPORTUNITY_TRANSITIONS``
* ``docs/OPEN_QUESTIONS.md`` Q-17 -- the Nigeria-Australia corridor link is an AI
  synthesis, and the UI must be able to show that
* ``docs/architecture/adr/0006-classification-zones.md`` -- ``classification``
* ``data/taxonomy/sectors.json`` -- the ``sector_code`` / ``sub_sector_code`` vocabulary

No ``relationship()`` is declared here. Every foreign key this table carries points at a
table owned by another model module (``users``, ``organisations``, ``stakeholders``,
``signals``, ``ai_traces``), so per the cross-module rule the column is declared by
table-name string and the ORM relationship, where one is wanted, is declared on the side
that owns the other table. That is what keeps this module free of sibling imports, and
``app.models`` acyclic.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import OPPORTUNITY_INITIAL, OpportunityStage
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "OPPORTUNITY_STAGE_ENUM",
    "Opportunity",
]

#: The one ``opportunity_stage`` Postgres enum type.
#:
#: Declared at module level for the same reason
#: :data:`app.models.mixins.CLASSIFICATION_ENUM` is: a Postgres enum type is global to the
#: schema, so a second column that also holds a stage (a ``previous_stage`` on a pipeline
#: projection, say) must reuse *this instance* rather than calling ``pg_enum`` again and
#: minting a rival object that competes to ``CREATE TYPE opportunity_stage``.
OPPORTUNITY_STAGE_ENUM: Final[SAEnum] = pg_enum(OpportunityStage, "opportunity_stage")


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, ClassifiedMixin, Base):
    """A trade or investment opportunity in the Nigeria-Australia corridor.

    An opportunity is the unit of work the mission actually pursues: something detected in
    the intelligence stream, judged real and in scope by a person, then advanced through a
    planned approach, a recorded contact, a meeting and a negotiation to either a
    partnership or a closure. It is deliberately the join point of the whole demo -- it
    names the signal it came from, the sector it sits in, the organisation it is with, the
    stakeholder who carries it and the officer who owns it -- so "signal to opportunity to
    stakeholder to meeting to outcome" can be walked without ever leaving this row.

    Invariants a reader must know:

    1. **Stage is table-driven and deny-by-default.** ``stage`` changes only via an event
       in ``app.domain.enums.OPPORTUNITY_TRANSITIONS``, fired by an authenticated human
       holding the permission listed in ``docs/workflows.md`` section 1. There is no "set
       the stage" path. ``PARTNERED`` and ``CLOSED`` are terminal and accept no further
       events; a revived opportunity is a new ``DETECTED`` row, never a resurrected one.
    2. **``stage_changed_at`` tracks the stage, not the row.** Editing a description does
       not touch it. That distinction is what makes "days in stage" -- the pipeline-ageing
       figure on the board -- honest, and it is why the column is not ``updated_at``.
    3. **Score and rationale are one thing.** A ``score`` without a ``score_rationale`` is
       a number nobody can defend, which is the failure mode this platform exists to avoid.
       Both are nullable together (an opportunity that has not been scored has neither) and
       the service layer writes them together.
    4. **AI provenance is a first-class fact.** ``is_proposed_by_ai`` says whether the
       corridor link this row asserts is a mission judgement or a platform synthesis, and
       ``proposal_trace_id`` says which Gateway call made it. See the column comments and
       ``docs/OPEN_QUESTIONS.md`` Q-17.
    5. **Closure always carries a reason.** Every event whose target is ``CLOSED`` is
       marked as requiring one in ``docs/workflows.md`` section 1; ``closed_reason`` is
       where that text lives, and the audit row references it rather than copying it
       (``docs/workflows.md`` section 0.10).
    6. **Classification only ever rises.** The mixin default is ``MISSION_INTERNAL``; the
       ``enter_negotiation`` event raises it to at least ``CONFIDENTIAL`` (``workflows.md``
       section 1, row 10), because a negotiating position is exactly what ADR-0006 protects.
       Nothing computes its way back down: a downgrade is a deliberate human act writing an
       ``object.reclassified`` audit row.
    7. **Nothing here is hard-deleted.** The foreign keys deliberately declare no
       ``ON DELETE`` behaviour, so the default ``NO ACTION`` applies: deleting a referenced
       user, organisation, stakeholder, signal or trace fails loudly instead of silently
       nulling or orphaning a link the "one governed picture" is drawn from.
    """

    __tablename__ = "opportunities"

    # -- What it is -------------------------------------------------------------------
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment=(
            "Short human headline, e.g. 'AU lithium value-chain partnership with Nigerian "
            "beneficiation'. Bounded rather than Text because it is rendered in a pipeline "
            "card and a brief line item, where an unbounded string is a layout bug waiting "
            "for a demo audience."
        ),
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "The full statement of the opportunity: what is being pursued, with whom, and "
            "why now. Unbounded because it carries the reasoning a reader needs, and NOT "
            "NULL because an opportunity nobody can describe cannot be qualified by anybody."
        ),
    )

    # -- Where it is in the pipeline --------------------------------------------------
    stage: Mapped[OpportunityStage] = mapped_column(
        OPPORTUNITY_STAGE_ENUM,
        nullable=False,
        default=OPPORTUNITY_INITIAL,
        index=True,
        comment=(
            "Pipeline stage (docs/workflows.md section 1). The default is the domain "
            "constant OPPORTUNITY_INITIAL, not a literal, so this column and the state "
            "machine cannot drift apart. DETECTED is the only state an opportunity may be "
            "created in; every later value is the result of a table-driven event fired by "
            "a human."
        ),
    )
    stage_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment=(
            "When the stage last changed -- NOT when the row last changed. Set by the "
            "workflow service on every accepted transition, in the same transaction as the "
            "state write and the audit row. It deliberately carries no onupdate: an edit to "
            "any other column must not reset the pipeline-ageing clock."
        ),
    )

    # -- Where it sits in the taxonomy ------------------------------------------------
    sector_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Top-level sector code from data/taxonomy/sectors.json (the entries with "
            "parent null), e.g. CRITICAL_MINERALS. A stable identifier that is never "
            "renamed; a plain code rather than a foreign key because the taxonomy is "
            "versioned seed data shared with the web client, not a mutable table."
        ),
    )
    sub_sector_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Optional child sector code whose parent is sector_code, e.g. CM_LITHIUM. "
            "Nullable because an opportunity may legitimately span a whole sector before "
            "it is narrowed."
        ),
    )
    country_focus: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        comment=(
            "ISO 3166-1 alpha-2, upper case: which side of the corridor the activity lands "
            "in (NG or AU for the demo). Bilateral work has two sides, so this records "
            "where the value is realised, not where the mission sits."
        ),
    )

    # -- What it is worth, and how confident we are -----------------------------------
    value_estimate_aud: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment=(
            "Estimated value in AUD. Numeric(18, 2) and never a float: binary floating "
            "point cannot represent 0.10, and a rounding artefact in a trade figure in "
            "front of an Ambassador is unrecoverable. Nullable because an unsized "
            "opportunity is a real state, distinct from one estimated at zero."
        ),
    )
    probability: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "Likelihood of reaching PARTNERED, as a PERCENTAGE from 0 to 100 (not a 0-1 "
            "fraction), bounded by ck_opportunities_probability_range. A human judgement, "
            "deliberately distinct from the AI-produced score."
        ),
    )
    score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment=(
            "Priority score from 0 to 100, bounded by ck_opportunities_score_range. "
            "Produced by the OPPORTUNITY_SCORE Gateway purpose and editable by an officer. "
            "NULL means not yet scored, which is why the qualify event requires a non-null "
            "score (docs/workflows.md section 1, row 2)."
        ),
    )
    score_rationale: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "The explainable breakdown behind score, written by app.services.scoring: "
            "{score, band, confidence, weighted_total, evidence_ceiling, capped_by_evidence, "
            "caveats, decision_support_only, factors[]} where each factor carries its "
            "weight, value, contribution, the sentence explaining it, its evidence_ids and "
            "- when an officer has adjusted it - the machine value it replaced and why. "
            "JSONB rather than JSON so the trace drawer can query into it. Structured "
            "rather than prose because the score is explainable AND editable factor by "
            "factor, and prose is neither. NULL exactly when score is NULL."
        ),
    )

    # -- Who and what it is attached to (every FK crosses a module boundary) ----------
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment=(
            "The officer accountable for advancing this opportunity. Nullable because a "
            "freshly DETECTED row may not be assigned yet; an unassigned opportunity in a "
            "later stage is a queue somebody has to work, which is why this is indexed."
        ),
    )
    lead_organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id"),
        nullable=True,
        comment=(
            "The counterpart organisation the opportunity is with. Nullable: an "
            "opportunity can be real before the counterpart has been identified."
        ),
    )
    primary_stakeholder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stakeholders.id"),
        nullable=True,
        comment=(
            "The individual who carries this relationship. The plan_contact event requires "
            "a linked stakeholder (docs/workflows.md section 1, row 4), so this is NULL "
            "only in the earliest stages."
        ),
    )
    source_signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("signals.id"),
        nullable=True,
        comment=(
            "The intelligence signal this opportunity was detected from; the detect event "
            "requires it (docs/workflows.md section 1, row 1) and it is the first link in "
            "the chain the demo walks. The column stays nullable so an officer can also "
            "raise an opportunity from their own knowledge rather than from the feed."
        ),
    )

    # -- Working state -----------------------------------------------------------------
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "When the next step on this opportunity falls due. Drives the 'needs attention' "
            "section of the morning brief. Nullable because not every live opportunity has "
            "a dated next step."
        ),
    )
    closed_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Why the opportunity was closed. Required by every event whose target is "
            "CLOSED, and capped at 500 characters by the service layer (docs/workflows.md "
            "section 0.10); the audit row references this column rather than copying the "
            "text into the log. NULL for any opportunity that is not CLOSED."
        ),
    )

    # -- Provenance: reported, or proposed? (OPEN_QUESTIONS Q-17) ----------------------
    is_proposed_by_ai: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "TRUE when the opportunity itself -- the assertion that these two sides of the "
            "corridor connect -- is a synthesis the platform PROPOSED, not a fact any "
            "source REPORTED. This is not decoration. OPEN_QUESTIONS Q-17 records that no "
            "public source links an Australian lithium operator to Nigeria, so the hero "
            "opportunity is AI-proposed and must render at LOWER confidence than the "
            "signals beneath it, badged 'AI-proposed, pending officer qualification'. A "
            "platform that cannot show the difference between what it read and what it "
            "inferred is a platform nobody should trust with a bilateral relationship. NOT "
            "NULL with a Python-side default only: a raw INSERT that omits it fails loudly "
            "rather than quietly claiming human provenance."
        ),
    )
    proposal_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_traces.id"),
        nullable=True,
        comment=(
            "The ai_traces row for the Gateway call that proposed this opportunity, so the "
            "trace drawer can show the prompt, the evidence and the fallback flag behind "
            "the proposal. Expected non-NULL whenever is_proposed_by_ai is TRUE; NULL for a "
            "human-raised opportunity."
        ),
    )

    __table_args__ = (
        # Each bound is written as one expression per column so a violation names the
        # column it came from. NULL is permitted by design: a CHECK passes when it
        # evaluates to NULL, so an unscored opportunity is unaffected and nullability
        # stays the job of the column rather than of the constraint.
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="probability_range",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="score_range",
        ),
        # The pipeline board reads WHERE stage = ? AND sector_code = ?, which is this
        # index. The name is omitted deliberately: NAMING_CONVENTION renders it as
        # ix_opportunities_stage_sector_code, which is the whole point of having a
        # convention. Note that its leftmost prefix also serves stage-only lookups, so the
        # single-column index created by stage's index=True is redundant for reads. It is
        # kept because the schema specification asks for both and it costs nothing at demo
        # volume, and it is the first index to drop if write amplification ever matters.
        Index(None, "stage", "sector_code"),
        {
            "comment": (
                "Trade and investment opportunities in the Nigeria-Australia corridor. "
                "Advanced only by the state machine in docs/workflows.md section 1."
            )
        },
    )

    def __repr__(self) -> str:
        """Return a debugging representation, deliberately omitting title and description.

        A ``__repr__`` lands in logs, tracebacks and the interactive shell, none of which
        are classified surfaces. An opportunity in ``NEGOTIATION`` is typically
        ``CONFIDENTIAL`` under ADR-0006, and its title alone can disclose an unannounced
        commercial intention, so only the identifier, the stage and the zone appear here.
        """
        return (
            f"Opportunity(id={self.id!r}, stage={self.stage.value!r}, "
            f"classification={self.classification.value!r})"
        )
