"""Response models for the Intelligence context -- the morning brief over HTTP.

These are the wire contract the web client is generated from, so every field carries a
description: the generated TypeScript keeps them as doc comments, and a brief is read by
people who will never open this file.

**Read-only.** ``BRIEF_TRANSITIONS`` and ``app.services.briefs.transition_brief`` already
implement DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED with an audit row per transition, but
W3.1 mounts no transition route. The status is *reported* here and moved elsewhere.

**One evidence shape, both writers** (Q-23, resolved by the architect 2026-09-14). Until
that ruling ``app.services.briefs.generate_brief`` emitted ``title`` and no ``quote`` while
``data/demo-seed/seed_parts/briefs.py`` emitted ``quote`` and no ``title``, so the same
brief rendered differently depending on which had produced the row. Both now write all six
keys: ``{citation_id, document_id, title, quote, url, publisher}``. The quote comes from the
cited entry's ``supports_claims`` in ``data/demo-seed/citations.json`` -- a sentence the
registry records that page as actually supporting, never a paraphrase and never a summary,
because a quotation an Ambassador cannot find on the page is attribution laundering.
``tests/test_seed_integrity.py`` now pins the shape and checks every quote against its
citation, so the two writers cannot separate again without a red test. The fields stay
nullable on the wire: a row written before the ruling is still readable, and a client must
degrade rather than crash.

**``confidence`` is a float on the wire and 0-100 in the column.** Pydantic v2 serialises
``Decimal`` as a JSON *string* -- ``schemas/opportunities.py`` types ``score: Decimal`` and
the generated client shows ``score?: string | null`` for exactly that reason. A confidence
the client has to parse before it can draw a bar is a bug waiting to be reintroduced
(``docs/W2_STATUS.md`` item 3), so the cast happens once, in the router's projection.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import BriefItemType, BriefStatus, Classification, RoleCode

__all__ = [
    "BriefEvidenceResponse",
    "BriefItemResponse",
    "BriefListResponse",
    "BriefResponse",
    "BriefSummaryResponse",
    "BriefTraceResponse",
]


class BriefEvidenceResponse(BaseModel):
    """One citation behind a brief item.

    ``citation_id`` is the only field guaranteed present: it is the key into
    ``data/demo-seed/citations.json`` that the stage 8 post-check resolved, and
    ``app.services.briefs.generate_brief`` refuses to persist an item citing an id that is
    not ``VERIFIED`` there. Everything else is nullable because the two writers of this
    column disagree (see the module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(description="Key into data/demo-seed/citations.json. Always present.")
    document_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The ingested documents row this citation was retrieved from, when there was "
            "one. Ties the public page to the captured text."
        ),
    )
    title: str | None = Field(
        default=None,
        description=(
            "Source title, used as the link label. Populated by both writers since Q-23. "
            "Still nullable so a row written before that ruling stays readable: fall back "
            "to publisher, then citation_id, and never render an empty link label."
        ),
    )
    quote: str | None = Field(
        default=None,
        description=(
            "The exact sentence on the public page this item rests on, verbatim from that "
            "citation's `supports_claims`. Populated by both writers since Q-23. Never a "
            "paraphrase: a quotation the reader cannot find on the page is attribution "
            "laundering. Null only on a row written before that ruling."
        ),
    )
    url: str | None = Field(
        default=None,
        description=(
            "A real public URL, never synthesised (BUILD_BIBLE section 11). Null means "
            "there is no openable page: render plain text, never a broken link."
        ),
    )
    publisher: str | None = Field(default=None, description="Who published the source.")


class BriefItemResponse(BaseModel):
    """One numbered entry on the brief: the claim, the judgement, and the evidence.

    ``body`` and ``so_what`` are deliberately separate fields and must be rendered apart.
    ``body`` is the sourced account and is what ``evidence`` supports; ``so_what`` is the
    mission's analytic judgement, which the evidence does not support and does not claim
    to. Collapsing the two is how a brief becomes a summary.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="Internal identifier (a ULID rendered as a UUID).")
    position: int = Field(description="Render order within the brief, ascending.")
    item_type: BriefItemType = Field(
        description="What this item points at: SIGNAL, OPPORTUNITY, CASE, MEETING or KNOWLEDGE."
    )
    classification: Classification = Field(
        description=(
            "The ADR-0006 zone of THIS item. An item you are not cleared for is absent "
            "from the list and absent from any count -- never redacted in place."
        )
    )
    headline: str = Field(description="The claim, in one line.")
    body: str = Field(
        description="What happened, according to the sources. This is what `evidence` supports."
    )
    so_what: str = Field(
        description=(
            "The mission's analytic judgement. NOT supported by `evidence`. Render it "
            "visibly apart from `body`."
        )
    )
    confidence: float | None = Field(
        default=None,
        description=(
            "How sure the mission is of this item, 0-100, or null when not assessed. "
            "A float rather than a decimal string, so a client can draw it without parsing."
        ),
    )
    is_proposed_by_ai: bool = Field(
        description=(
            "True when this item rests on an opportunity the platform PROPOSED rather than "
            "one a source REPORTED (OPEN_QUESTIONS Q-17). Joined from "
            "opportunities.is_proposed_by_ai -- brief_items carries no provenance column of "
            "its own. Such an item must render at visibly lower confidence than the "
            "evidenced items beside it; that contrast is the payoff of winning moment #1."
        )
    )
    signal_id: uuid.UUID | None = Field(
        default=None, description="The signal this item reports, when it is a SIGNAL."
    )
    opportunity_id: uuid.UUID | None = Field(
        default=None,
        description="The opportunity this item reports, when it is an OPPORTUNITY.",
    )
    case_id: uuid.UUID | None = Field(
        default=None, description="The consular case this item reports, when it is a CASE."
    )
    meeting_id: uuid.UUID | None = Field(
        default=None, description="The meeting this item reports, when it is a MEETING."
    )
    evidence: list[BriefEvidenceResponse] = Field(
        description=(
            "Ordered citations behind `body`. Never empty for a generated item: "
            "app.services.briefs refuses to persist an uncited one."
        )
    )


class BriefTraceResponse(BaseModel):
    """The BUILD_BIBLE section 4a routing decision behind an AI-generated brief.

    Present only when the caller holds ``read:ai_trace`` AND clears
    ``dominant(data_class, result_class)`` -- the same two gates
    ``GET /v1/ai/traces/{trace_id}`` applies, re-applied by
    ``app.services.briefs.trace_for_brief`` so that embedding the badge here is not a
    side-channel around them. When the caller fails either gate the field is ``null`` while
    ``BriefResponse.trace_id`` stays populated: the routing decision exists, and this role
    may not inspect it.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: uuid.UUID = Field(description="The ai_traces row this brief was generated by.")
    route_badge: str = Field(
        description=(
            "The section 4a badge exactly as the Gateway rendered it at stage 5, e.g. "
            "'INTERNAL - external-noret - claude-sonnet-5'. Render it as an opaque string. "
            "Do NOT parse it: there are five badge shapes with three or four segments and "
            "the first segment is not always a classification display name. Empty when the "
            "row predates the column -- render 'routing not recorded', never rebuild it."
        )
    )
    data_class: Classification = Field(description="The zone the call was declared to run in.")
    result_class: Classification = Field(description="The zone of the answer that came back.")
    model_route: str = Field(description="The route chosen at stage 5.")
    route_reason: str = Field(description="One sentence saying why that route was chosen.")
    model_requested: str | None = Field(
        default=None, description="The model the route asked for, if any."
    )
    model_used: str | None = Field(
        default=None,
        description=(
            "The model that actually answered. Null on a fallback: a deterministic snapshot "
            "was authored by no model, and naming one would be a fabrication."
        ),
    )
    fallback: bool = Field(
        description=(
            "True when the deterministic snapshot was served instead of a live call "
            "(CLAUDE.md rule 2.5). Render it as its own chip, never folded into the badge."
        )
    )
    fallback_reason: str | None = Field(
        default=None, description="Why the fallback was used, when it was."
    )


class BriefResponse(BaseModel):
    """Today's morning brief for one caller. Winning moment #1."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="Internal identifier (a ULID rendered as a UUID).")
    brief_date: date = Field(description="The mission day this brief covers.")
    is_today: bool = Field(
        description=(
            "True when brief_date is the API's current UTC date. False means this is the "
            "most recent brief that exists, not this morning's -- say so on screen rather "
            "than letting a reader assume it is current."
        )
    )
    role_scope: RoleCode | None = Field(
        description=(
            "The role this brief was assembled for. Null is the mission-wide brief, which "
            "is what a role with no brief of its own receives."
        )
    )
    is_mission_wide: bool = Field(
        description=(
            "True when role_scope is null. Say so on screen: a reader is entitled to know "
            "they are looking at the mission brief rather than one built for their desk."
        )
    )
    title: str = Field(description="The brief's headline.")
    summary: str = Field(description="The standfirst, above the items.")
    status: BriefStatus = Field(
        description=(
            "DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED. A DRAFT has not been through "
            "human hands and the screen must say so."
        )
    )
    classification: Classification = Field(
        description=(
            "The brief's own stored zone. Items carry their own and are filtered "
            "independently -- this value does not vouch for them."
        )
    )
    generated_by: str = Field(description="SYSTEM, AI or HUMAN.")
    trace_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The generating ai_traces row. Non-null with `trace` null means the routing "
            "decision exists but this role may not inspect it."
        ),
    )
    trace: BriefTraceResponse | None = Field(
        default=None,
        description="The routing decision, when this caller may see it. See BriefTraceResponse.",
    )
    created_at: datetime = Field(description="When the brief row was written.")
    items: list[BriefItemResponse] = Field(
        description="In position order, narrowed to the zones this caller is cleared to read."
    )


class BriefSummaryResponse(BaseModel):
    """One brief in the history rail: no items, no evidence, no trace."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(description="Internal identifier (a ULID rendered as a UUID).")
    brief_date: date = Field(description="The mission day this brief covers.")
    role_scope: RoleCode | None = Field(
        description="The role it was assembled for. Null is the mission-wide brief."
    )
    is_mission_wide: bool = Field(description="True when role_scope is null.")
    title: str = Field(description="The brief's headline.")
    status: BriefStatus = Field(description="Its workflow state.")
    classification: Classification = Field(description="Its stored zone.")
    generated_by: str = Field(description="SYSTEM, AI or HUMAN.")
    is_today: bool = Field(description="True when brief_date is the API's current UTC date.")


class BriefListResponse(BaseModel):
    """The caller's brief history, newest first.

    ``total`` counts only what this caller may read, and comes from the same statement that
    returned the rows. A total computed without the clearance predicate would tell the
    reader exactly how many briefs they are not cleared for, which is the leak
    ``app.security.deps.readable_classifications`` exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[BriefSummaryResponse] = Field(description="The briefs, newest first.")
    total: int = Field(description="How many were returned. Never a count of hidden rows.")
