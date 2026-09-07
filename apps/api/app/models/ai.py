"""AI observability: one row per AI Gateway call, and nothing else.

One table, ``ai_traces``, written by stage 9 of the Gateway pipeline (ADR-0001). It is the
only durable evidence that an AI answer was produced under the rules the system claims to
enforce, and it is what the UI trace drawer renders. ``BUILD_BIBLE.md`` section 5 is
explicit that *the Gateway's routing decision plus the audit trail must be visible in the
UI* -- that visibility is what makes a security-minded consular buyer trust the product,
so every column here exists to be read by a human, not merely stored.

**This module must never import the ``anthropic`` SDK, and neither must anything it
imports.** ADR-0001 makes ``app/ai/gateway.py`` the single file permitted to touch a model
provider. This is a persistence model: it describes a call that already happened. If a
future edit finds itself wanting a provider type here, the type it actually wants is a
string.

**Relationship to ``audit_events``.** They are different records and both are needed.
``audit_events`` (ADR-0004) answers *who decided what, and was it allowed*; ``ai_traces``
answers *how was this generated, from what evidence, and did the model actually run*. An
audit row carries ``trace_id`` pointing here when AI informed the decision, and the fact
that most audit rows have a NULL ``trace_id`` is itself informative. The two are written in
the same transaction as the change they describe.

Cross-module foreign key emitted by this module, declared by table-name string with no
import of the owning module: ``ai_traces.user_id -> users.id`` (governance).

Five sibling modules point *at* this table -- ``audit_events.trace_id``,
``briefs.trace_id``, ``meetings.pre_read_trace_id``, ``meetings.followup_trace_id``,
``opportunities.score_trace_id`` and ``cases`` -- and none of them declares a
``relationship()``, because the other side lives in a different module. Neither does this
module, for the same reason: a ``relationship()`` here would require importing a sibling
model module, and import cycles between model modules are forbidden. The foreign keys are
all the database needs; a service that wants the join writes it explicitly.
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
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode
from app.models.base import Base, pg_enum
from app.models.mixins import CLASSIFICATION_ENUM, UUIDPrimaryKeyMixin

__all__ = [
    "AI_PURPOSE_ENUM",
    "APPROVAL_STATUS_ENUM",
    "FALLBACK_REASONS",
    "ROLE_CODE_ENUM",
    "AiTrace",
]

# ---------------------------------------------------------------------------
# Native enum types
# ---------------------------------------------------------------------------
#
# Declared at module level rather than inline, for the reason ``mixins.CLASSIFICATION_ENUM``
# gives: a Postgres enum type name is global to the schema, so two ``pg_enum`` calls for the
# same name are two SQLAlchemy objects competing to ``CREATE TYPE`` it.
#
# ``ai_purpose`` is owned here and declared nowhere else.
#
# ``role_code`` and ``approval_status`` are NOT owned here. ``role_code`` is also declared in
# ``governance.py`` and ``intelligence.py``; ``approval_status`` is also declared in
# ``meetings.py``. Sharing one instance would mean importing a sibling model module, which the
# cross-module rule forbids outright, so each module declares its own -- the established
# pattern in this codebase, and a deliberate trade of DDL tidiness for an acyclic import graph.
#
# What that costs, stated plainly so it is not discovered during a migration review. Both
# halves below were measured against the live Postgres 16 container, not reasoned about:
#
#   * ``metadata.create_all()`` is FINE, and fine even with ``checkfirst=False``. SQLAlchemy
#     memoises a named type within a single create run, so N instances of ``role_code``
#     produce exactly one ``CREATE TYPE``. Nothing needs doing for tests or ``demo-reset``.
#   * Alembic is NOT fine. It emits one ``op.create_table`` per table, each carrying its own
#     inline ``sa.Enum``, and that memo does not span separate operations. The second table
#     to mention ``role_code`` fails with ``DuplicateObject: type "role_code" already
#     exists``. So the FIRST migration to create these types must be hand-edited: create each
#     type once up front, and pass ``create_type=False`` on the inline enums.
#
# ``classification`` is exempt from all of this because the shared ``CLASSIFICATION_ENUM``
# instance is imported from ``mixins``, which is not a model module and so creates no cycle.
# Recorded as an integration task for whoever writes migration 0001, not a defect in any one
# model module -- no module can fix it alone without importing a sibling.
AI_PURPOSE_ENUM: Final[SAEnum] = pg_enum(AiPurpose, "ai_purpose")
ROLE_CODE_ENUM: Final[SAEnum] = pg_enum(RoleCode, "role_code")
APPROVAL_STATUS_ENUM: Final[SAEnum] = pg_enum(ApprovalStatus, "approval_status")


#: The closed vocabulary for :attr:`AiTrace.fallback_reason`, as specified for this table.
#:
#: Deliberately a tuple of plain strings and NOT a native Postgres enum, because the
#: vocabulary is not yet settled. ADR-0002's failure-mode table names the same seven modes
#: with five different spellings (``PROVIDER_ERROR``, ``RATE_LIMITED``, ``NO_CREDENTIAL``,
#: ``DEMO_MODE``, ``CITATION_INVALID``), and ``app/domain/enums.py`` declares no
#: ``FallbackReason`` at all. Minting a native enum type from one of two competing lists
#: would freeze the wrong one into DDL and make the correction a migration; a ``String(64)``
#: keeps the column honest until the Gateway lands and one list wins. When it does, this
#: tuple moves to ``app/domain/enums.py`` as a real enum and the column type changes with it.
#:
#: Until then, treat this as the constant to validate against in the Gateway rather than
#: writing the literals at the call site.
FALLBACK_REASONS: Final[tuple[str, ...]] = (
    "TIMEOUT",
    "API_ERROR",
    "RATE_LIMIT",
    "SCHEMA_INVALID",
    "NO_API_KEY",
    "LIVE_DISABLED",
    "CITATION_CHECK_FAILED",
)


class AiTrace(UUIDPrimaryKeyMixin, Base):
    """One AI Gateway call, recorded so a sceptical reader can audit it (ADR-0001 stage 9).

    Every ``gateway.generate(...)`` writes exactly one row, whatever the outcome: a clean
    live generation, a snapshot served because the provider timed out, and a refusal at the
    classification gate all appear here. That completeness is the point. A trace table that
    records only successes tells an auditor nothing, and "the demo never dead-ends"
    (``BUILD_BIBLE.md`` section 0) is only a credible claim if the failures are visible too.

    **What the trace drawer shows, and why each half is here.** The drawer is a Week 3
    deliverable and this row is its entire data source: the purpose, both classification
    zones, the model that was chosen and the sentence explaining why, whether the call was
    live, whether a fallback was served and for what reason, the evidence IDs the answer was
    grounded in, and a per-stage record of the nine-stage pipeline. Together they let a
    consular buyer answer "why did it say that, and could it have seen something it
    shouldn't" without reading any code.

    **Two classifications, not one, and they legitimately differ.** ``data_class`` is the
    zone declared for the *request*; ``result_class`` is the zone of the *output* after
    ADR-0006 propagation, which takes the maximum over everything the answer was built from.
    A ``MISSION_INTERNAL`` question answered from a ``CONFIDENTIAL`` document produces a
    ``CONFIDENTIAL`` result however anodyne its prose reads -- a summary of a secret is a
    secret. The drawer renders both so that escalation is visible rather than implicit.

    ``result_class`` is also the zone that governs who may read this row. That is why the
    class does not use :class:`~app.models.mixins.ClassifiedMixin`: a third
    ``classification`` column would be a second source of truth for the same question, and
    two answers to "how sensitive is this trace" is one answer too many. Both columns use the
    shared :data:`~app.models.mixins.CLASSIFICATION_ENUM` instance, so this table points at
    the same four-label Postgres type as every other classified table.

    **The raw prompt is never stored.** ``prompt_hash`` holds a digest and that is all. An
    assembled prompt contains the retrieved evidence, which for a consular purpose is
    ``CONSULAR_SENSITIVE`` citizen material; persisting it would quietly make this
    observability table the least-protected copy of the most-protected data in the system --
    exactly the mistake ADR-0004 forbids for ``audit_events`` payloads, for the same reason.
    The hash still supports the questions worth asking (was this the identical prompt as
    that one, did the prompt change between two runs) without holding the content. The same
    rule governs ``error``: a message, never a provider echo of the request.

    **No ``updated_at``, and therefore no** :class:`~app.models.mixins.TimestampMixin`.
    A trace is a record of something that already happened, and it is never edited. Nothing
    about a completed call can change afterwards: the model that answered, the latency it
    took and the evidence it used are settled facts. An ``updated_at`` column would imply
    otherwise and would eventually attract code that acts on the implication. Corrections, if
    a stage is ever found to have recorded the wrong thing, are made by writing a fresh trace
    and an ``audit_events`` row that references both -- never by editing this one.

    Unlike ``audit_events`` this table carries no database-level immutability trigger, and
    that difference is deliberate rather than an oversight: ADR-0004 scopes append-only
    enforcement to the audit log, whose evidentiary weight justifies the operational cost.
    ``ai_traces`` is append-only *by convention and by the absence of any code that updates
    it*. If a trace ever becomes load-bearing in a dispute, promote it to the ADR-0004
    treatment deliberately.

    **Reading ``live`` and ``fallback`` together.** They are independent booleans and all
    four combinations occur, so neither alone tells the story:

    * ``live=True,  fallback=False`` -- a live model call that validated and cited cleanly.
    * ``live=True,  fallback=True``  -- a live call was attempted and something went wrong;
      ``fallback_reason`` says what (``TIMEOUT``, ``SCHEMA_INVALID``, ...).
    * ``live=False, fallback=True``  -- no call was attempted at all, by configuration or
      for want of a credential (``LIVE_DISABLED``, ``NO_API_KEY``). This is the rehearsal
      path of ADR-0002 and it is a first-class state, not a degraded one.
    * ``live=False, fallback=False`` -- the call never reached generation. A refusal at
      stage 2 (the caller may not read the zone) or a snapshot citing evidence this caller
      is not authorised for both land here, with ``error`` set and ``approval_status``
      ``BLOCKED``. ADR-0002 is explicit that such a snapshot is refused, not downgraded.

    Ordering: ``id`` is a ULID, so ``ORDER BY id`` is a valid chronological total order
    (ADR-0007). ``created_at`` is the semantic timestamp and is indexed for the range and
    "latest N" queries the drawer runs.
    """

    __tablename__ = "ai_traces"

    __table_args__ = (
        # A fallback without a stated reason is an unexplained fallback, and an unexplained
        # fallback is precisely what ADR-0002 exists to prevent ("the failure must be
        # honest"). The converse half matters just as much: a reason on a row that did not
        # fall back means one of the two columns is lying, and there is no way to tell
        # which. Both booleans are NOT NULL and `IS NOT NULL` never yields NULL, so this
        # expression is total -- there is no third outcome for the constraint to permit.
        CheckConstraint(
            "fallback = (fallback_reason IS NOT NULL)",
            name="fallback_reason_iff_fallback",
        ),
        # Measurements, not opinions. A negative latency or token count is a bug in the
        # instrumentation, and catching it at write time is far cheaper than explaining a
        # nonsensical figure in the trace drawer during a demo.
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="latency_ms_non_negative",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="input_tokens_non_negative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="output_tokens_non_negative",
        ),
        # NOTE for anyone tempted to add `CHECK (result_class >= data_class)` to enforce
        # ADR-0006's "propagation only ever escalates": it would be WRONG. Postgres orders an
        # enum by DECLARATION order, which here is PUBLIC < MISSION_INTERNAL < CONFIDENTIAL <
        # CONSULAR_SENSITIVE, while CLASSIFICATION_RANK in app/domain/enums.py ranks
        # CONSULAR_SENSITIVE (20) BELOW CONFIDENTIAL (30). The two orders disagree on exactly
        # the pair that matters, so the constraint would reject a legitimate
        # CONSULAR_SENSITIVE result of a CONFIDENTIAL request and accept its inverse.
        # Verified against the container: pg_enum sort order is
        # [PUBLIC, MISSION_INTERNAL, CONFIDENTIAL, CONSULAR_SENSITIVE] while rank order is
        # [PUBLIC, MISSION_INTERNAL, CONSULAR_SENSITIVE, CONFIDENTIAL]. The invariant is real
        # and belongs in the Gateway, which has `dominant()` and the rank map; expressing it
        # in SQL first needs a rank function in the database.
        #
        # (purpose, created_at) answers "the last N calls for this purpose", which is both
        # the drawer's default view and the query behind the fallback-rate check during
        # rehearsal. `purpose` is the leading column, so this index also serves a bare
        # `WHERE purpose = ...` lookup -- a separate single-column index on `purpose` would
        # be strictly redundant, so there deliberately is not one.
        Index(None, "purpose", "created_at"),
        # (fallback, created_at) answers "did anything fall back in this run, and when",
        # which is the question asked immediately after a demo. Same leading-column argument:
        # this covers `WHERE fallback IS TRUE` on its own.
        Index(None, "fallback", "created_at"),
        {
            "comment": (
                "One row per AI Gateway call (ADR-0001 stage 9), including refusals and "
                "fallbacks. Append-only by convention: never UPDATEd. Renders the UI trace "
                "drawer (BUILD_BIBLE section 5). Holds no prompt text and no document text."
            )
        },
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment=(
            "When the call completed, on the database clock. There is deliberately no "
            "updated_at: a trace records something that already happened and is never "
            "edited. Indexed on its own because the composite indexes lead with another "
            "column and so cannot serve a plain time-range scan."
        ),
    )

    purpose: Mapped[AiPurpose] = mapped_column(
        AI_PURPOSE_ENUM,
        nullable=False,
        comment=(
            "The registered Gateway purpose (ADR-0001 stage 1). A closed allowlist: the "
            "purpose selects the prompt, the retrieval scope, the output schema and the "
            "fallback snapshot, so an unregistered purpose has no fallback and must not run."
        ),
    )
    scenario: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "The scenario half of the ADR-0002 snapshot key "
            "(data/demo-seed/ai_snapshots/{purpose}_{scenario}.json), a pure function of "
            "(purpose, role, primary object). Recorded on every call, not only fallbacks, so "
            "a missing snapshot can be diagnosed from a successful run. NULL where the "
            "purpose derives no scenario. '__default__' is a real value, not a placeholder."
        ),
    )

    # The column type is stated explicitly because SQLAlchemy skips `type_annotation_map` for
    # any column carrying a ForeignKey and resolves the type from the referenced column
    # instead -- which leaves this one as NullType whenever `app.models.governance` has not
    # been imported. `app.models.base` always imports it so DDL is never affected, but stating
    # the type means this module can be imported and inspected on its own, which is exactly
    # what happens while sibling modules are still being written.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "Who the call ran as. NULL for system callers -- the seed loader, a scheduled "
            "job, a warm-up -- which have no human principal and must not borrow one. ON "
            "DELETE RESTRICT: a user with traces cannot be deleted out from under them, so "
            "'who asked for this' stays answerable."
        ),
    )
    actor_role: Mapped[RoleCode | None] = mapped_column(
        ROLE_CODE_ENUM,
        nullable=True,
        comment=(
            "The role the caller was acting in AT THE TIME, denormalised for the same reason "
            "audit_events.actor_role is: role assignments change, and resolving the role by "
            "joining user_roles later would silently rewrite history after a promotion. The "
            "role is also an input to retrieval authorisation, so the trace must show which "
            "one was used. NULL alongside a NULL user_id."
        ),
    )

    data_class: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.MISSION_INTERNAL,
        comment=(
            "Classification of the REQUEST: the zone declared by the caller and checked "
            "against their clearance at ADR-0001 stage 2. Defaults to MISSION_INTERNAL and "
            "never to PUBLIC, matching dominant()'s answer for an empty part list -- an "
            "unknown zone fails closed (ADR-0006 point 6)."
        ),
    )
    result_class: Mapped[Classification] = mapped_column(
        CLASSIFICATION_ENUM,
        nullable=False,
        default=Classification.MISSION_INTERNAL,
        comment=(
            "Classification of the OUTPUT after ADR-0006 propagation: the maximum over "
            "everything the answer was built from, so it may be strictly higher than "
            "data_class when retrieval pulled in a more sensitive document. This is also the "
            "zone that governs who may read this trace row. Propagation never lowers a zone; "
            "a downgrade is a human act writing an object.reclassified audit row."
        ),
    )

    model_route: Mapped[str] = mapped_column(
        String(96),
        nullable=False,
        comment=(
            "The routing DECISION taken at ADR-0001 stage 5, before the call. NOT NULL "
            "because BUILD_BIBLE section 5 requires the routing decision to be visible in "
            "the trace drawer, and a nullable column is a column that ends up empty. A route "
            "was chosen even when no call followed, so a refusal still records one."
        ),
    )
    route_reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Why that route, in a sentence a non-engineer can read: routing is a function of "
            "purpose and of the highest-classification item in the assembled context "
            "(ADR-0006). Unbounded Text because it is prose. This sentence is the single "
            "most persuasive thing in the drawer -- it is the difference between showing a "
            "decision and asserting one -- so it is NOT NULL and must never be boilerplate."
        ),
    )
    budget_seconds: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
        comment=(
            "Wall-clock budget this call was given, in seconds. PER PURPOSE, not global: "
            "a purpose returning a score gets ~4s, one generating prose gets 25s. Recorded "
            "because 'it fell back' means something different at 4s than at 25s, and a "
            "reader cannot tell which without knowing what the call was actually allowed."
        ),
    )
    model_tier: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment=(
            "BUILD_BIBLE section 4a capability tier: 'fast' for classify/score, 'strong' "
            "for briefs and meeting prep. Secondary to sensitivity - the band decides "
            "whether a model may be asked at all, the tier only decides which one."
        ),
    )
    route_badge: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        default="",
        comment=(
            "The section 4a trace-drawer badge, e.g. 'PUBLIC · external · "
            "claude-sonnet-5 · strong'. Stored rendered rather than assembled by the "
            "UI: 4a requires it to be legible to a non-technical Ambassador, and a drawer "
            "that re-derives it can drift from what the Gateway actually decided."
        ),
    )
    model_requested: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "Provider model id the route asked for, e.g. a dated Anthropic model string. "
            "NULL when no call was attempted. Distinct from model_used so that a silent "
            "provider substitution or alias resolution is visible rather than invisible."
        ),
    )
    model_used: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "Provider model id that actually answered, as reported by the response. NULL "
            "when no call was attempted, when it failed before responding, and on a fallback "
            "-- a snapshot was authored by no model, and naming one would be a fabrication "
            "in the very table that exists to prevent fabrication."
        ),
    )

    live: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment=(
            "True if a real provider call was ATTEMPTED, regardless of whether it succeeded. "
            "Deliberately has no default: the Gateway always knows this and a default would "
            "let a caller omit the single most important honesty flag in the row. Read it "
            "with fallback -- all four combinations are meaningful (see the class docstring)."
        ),
    )
    fallback: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "True if a deterministic snapshot was served instead of a live answer (ADR-0002). "
            "Defaults to False so the honest value is the one that requires no action. A "
            "fallback is not a bypass: the classification gate, the evidence re-check and "
            "approval_status all still applied."
        ),
    )
    fallback_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "Why the fallback fired. One of app.models.ai.FALLBACK_REASONS: TIMEOUT, "
            "API_ERROR, RATE_LIMIT, SCHEMA_INVALID, NO_API_KEY, LIVE_DISABLED, "
            "CITATION_CHECK_FAILED. NULL if and only if fallback is False, enforced by "
            "ck_ai_traces_fallback_reason_iff_fallback. String rather than a native enum "
            "because ADR-0002's table spells five of these differently (PROVIDER_ERROR, "
            "RATE_LIMITED, NO_CREDENTIAL, DEMO_MODE, CITATION_INVALID) and the conflict is "
            "unresolved; freezing either list into DDL would make the correction a migration."
        ),
    )

    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Wall-clock milliseconds for the whole call, the number measured against "
            "ADR-0002's hard 4000 ms budget. Integer milliseconds, not a float: sub-"
            "millisecond precision is noise here and a float would render as 812.0000001 in "
            "the drawer. NULL when nothing was timed."
        ),
    )
    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Prompt tokens reported by the provider. NULL on a fallback or a refusal, where "
            "no provider counted anything; zero would be a different and false claim."
        ),
    )
    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Completion tokens reported by the provider. NULL for the same reasons.",
    )

    prompt_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "SHA-256 hex digest of the assembled prompt. THE RAW PROMPT IS NEVER STORED, "
            "here or anywhere: an assembled prompt embeds the retrieved evidence, which for "
            "a consular purpose is CONSULAR_SENSITIVE citizen material, and persisting it "
            "would make this observability table the least-protected copy of the most-"
            "protected data in the system. The digest still answers the questions worth "
            "asking -- was this the same prompt, did it change between runs -- without "
            "holding the content. NULL when no prompt was assembled."
        ),
    )
    retrieval_filter: Mapped[dict[str, Any]] = mapped_column(
        nullable=False,
        default=dict,
        comment=(
            "The authorisation filter applied BEFORE retrieval at ADR-0001 stage 3: the "
            "clearance, compartments and zone predicate that bounded the candidate set. "
            "Recorded because 'the filter ran in the query, not after it' (CLAUDE.md section "
            "5) is otherwise an unverifiable claim -- this column is the evidence for it. "
            "JSONB so the drawer can query it. An empty object means no filter was recorded, "
            "which is a finding, not a default."
        ),
    )
    evidence_ids: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Stable evidence IDs the answer was grounded in, in the order they were bound at "
            "stage 4. Every one was inside the authorised set of stage 3 and survived the "
            "stage 8 citation post-check. An empty list on a successful generation means an "
            "ungrounded answer, which is what winning moment #1 ('not a chatbot') exists to "
            "make impossible. JSONB, so `evidence_ids @> '[\"doc-123\"]'` finds every answer "
            "that leaned on a given document."
        ),
    )

    output_schema_name: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "Name of the Pydantic schema the response was validated against at stage 7 -- "
            "the purpose's contract. Recorded rather than inferred from purpose because "
            "schemas are versioned and a trace must say which one actually ran. NULL where "
            "generation never happened."
        ),
    )
    schema_valid: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment=(
            "Outcome of stage 7. Three-valued on purpose: True passed, False failed and "
            "triggered a fallback, NULL means the check never ran. Collapsing NULL into "
            "False would report a validation failure that never occurred."
        ),
    )
    citation_check_passed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment=(
            "Outcome of stage 8: every cited evidence ID existed and was in the caller's "
            "authorised set. False means a hallucinated or unauthorised citation was caught "
            "and the response refused -- the single most demonstrable control in the "
            "product. NULL means the check never ran, not that it passed."
        ),
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        APPROVAL_STATUS_ENUM,
        nullable=False,
        default=ApprovalStatus.NOT_REQUIRED,
        comment=(
            "Set by the Gateway, never by the caller (ADR-0001). PENDING_APPROVAL for "
            "purposes that produce a consequential artefact (MEETING_FOLLOWUP, "
            "CONSULAR_TRIAGE): the artefact persists as a draft and cannot be actioned until "
            "a human with the right permission approves it. BLOCKED is what a BUILD_BIBLE "
            "section 6 control returns when it refuses -- winning moment #2, made visible. A "
            "fallback still carries its purpose's status; a snapshot is not an approval."
        ),
    )

    stages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment=(
            "One object per stage of the fixed nine-stage pipeline (ADR-0001), in execution "
            "order: {stage, ok, detail, ms}. This is what turns the drawer from a summary "
            "into an explanation -- a reader can see the classification gate pass, retrieval "
            "return N candidates under the recorded filter, and the citation check reject a "
            "claim. A short list is itself the diagnosis: the pipeline stopped where the "
            "list stops. JSONB rather than a child table because it is a small, ordered, "
            "write-once list that is only ever read whole, alongside its parent."
        ),
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Human-readable failure detail when the call did not complete normally: the "
            "refusal reason, the validation error, the provider's message. A MESSAGE, never "
            "a provider echo of the request and never document text -- the prompt_hash rule "
            "applies here too. NULL on a clean call."
        ),
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Correlates this trace with the structlog request log and with every "
            "audit_events row from the same HTTP request. NOT NULL and matching "
            "audit_events.request_id exactly: a non-HTTP caller mints its own correlation id "
            "rather than leaving the chain broken. This is the join that lets an auditor "
            "reconstruct one request end to end."
        ),
    )
