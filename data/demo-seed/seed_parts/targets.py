"""Volume targets (BUILD_BIBLE section 10) and the report ``make seed`` prints.

``data/demo-seed/README.md`` section 3 requires the loader to print actual counts against
the targets and **fail** when one is missed. That is not ceremony: a silent under-seed
produces an empty-looking tile at the worst possible moment, and an empty tile in front of
an Ambassador cannot be explained away afterwards.

Two kinds of row are printed. **Bounded** rows carry a target range and fail the run when
they fall outside it. **Informational** rows are counted and shown but not policed --
nobody specified how many interactions the demo needs, and inventing a bound would turn a
judgement call into a build break.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select

from app.models.ai import AiTrace
from app.models.consular import Case, CaseEvent, CaseEvidence
from app.models.diaspora import DiasporaExpertise, DiasporaProfile, ExpertiseTag
from app.models.governance import AuditEvent, Permission, Role, RolePermission, User, UserRole
from app.models.intelligence import Brief, BriefItem, Document, Signal, Source
from app.models.knowledge import KnowledgeArticle
from app.models.meetings import Action, Meeting, MeetingAttendee, MeetingFollowup
from app.models.opportunities import Opportunity
from app.models.stakeholders import Interaction, Organisation, Stakeholder
from seed_parts.context import SEED_MARKER, SeedContext

__all__ = ["TargetRow", "report"]


@dataclass(frozen=True, slots=True)
class TargetRow:
    """One line of the final report."""

    table: str
    count: int
    minimum: int | None
    maximum: int | None
    note: str = ""

    @property
    def is_bounded(self) -> bool:
        """Whether this row can fail the run."""
        return self.minimum is not None or self.maximum is not None

    @property
    def ok(self) -> bool:
        """Whether the count is inside its target range."""
        if self.minimum is not None and self.count < self.minimum:
            return False
        return not (self.maximum is not None and self.count > self.maximum)

    @property
    def target_label(self) -> str:
        """The target, rendered for the report."""
        if not self.is_bounded:
            return "-"
        if self.minimum == self.maximum:
            return str(self.minimum)
        return f"{self.minimum}-{self.maximum}"


#: Bounded targets. The section 10 figures, plus the security vocabulary counts, which are
#: derived from ``app.security`` and would silently drop rows if the derivation broke.
_BOUNDED: Final[tuple[tuple[str, type, int, int, str], ...]] = (
    ("signals", Signal, 20, 30, "BUILD_BIBLE section 10"),
    ("opportunities", Opportunity, 12, 15, "BUILD_BIBLE section 10"),
    ("stakeholders", Stakeholder, 30, 30, "BUILD_BIBLE section 10"),
    ("cases", Case, 15, 15, "BUILD_BIBLE section 10"),
    ("diaspora_profiles", DiasporaProfile, 40, 40, "BUILD_BIBLE section 10"),
    ("knowledge_articles", KnowledgeArticle, 20, 20, "BUILD_BIBLE section 10"),
    ("meetings", Meeting, 10, 10, "BUILD_BIBLE section 10"),
    ("users", User, 6, 6, "app.security.principal.DEMO_PERSONAS"),
    ("roles", Role, 6, 6, "app.domain.enums.RoleCode"),
    ("permissions", Permission, 40, 40, "app.security.permissions.PERMISSION_SPECS"),
)

#: Counted and shown, never policed.
_INFORMATIONAL: Final[tuple[tuple[str, type, str], ...]] = (
    ("sources", Source, "one per publisher of a VERIFIED citation"),
    ("documents", Document, "one per VERIFIED citation"),
    ("organisations", Organisation, "real public actors only"),
    ("interactions", Interaction, "contact history behind last_contact_at"),
    ("meeting_attendees", MeetingAttendee, ""),
    ("meeting_followups", MeetingFollowup, "insert-once; never deleted (Q-05)"),
    ("actions", Action, ""),
    ("case_events", CaseEvent, "append-only"),
    ("case_evidence", CaseEvidence, ""),
    ("briefs", Brief, "mission-wide plus role-scoped"),
    ("brief_items", BriefItem, "every evidence entry resolves"),
    ("expertise_tags", ExpertiseTag, "data/taxonomy/expertise_tags.json"),
    ("diaspora_expertise", DiasporaExpertise, ""),
    ("ai_traces", AiTrace, "one per AI-badged artefact"),
    ("role_permissions", RolePermission, "app.security.matrix.ROLE_PERMISSIONS"),
    ("user_roles", UserRole, ""),
)


def report(
    ctx: SeedContext, audit_target: int, audit_window_days: int
) -> tuple[list[TargetRow], bool]:
    """Build the report rows and say whether every bounded target was met."""
    rows: list[TargetRow] = [
        TargetRow(table, ctx.count(model), minimum, maximum, note)
        for table, model, minimum, maximum, note in _BOUNDED
    ]

    marker = func.jsonb_extract_path_text(AuditEvent.payload, "seed_marker")
    seeded_audit = int(
        ctx.session.scalar(
            select(func.count()).select_from(AuditEvent).where(marker == SEED_MARKER)
        )
        or 0
    )
    tolerance = max(round(audit_target * 0.05), 5)
    rows.append(
        TargetRow(
            "audit_events (seeded)",
            seeded_audit,
            audit_target - tolerance,
            audit_target + tolerance,
            f"OPEN_QUESTIONS Q-13: ~{audit_target} over {audit_window_days} days",
        )
    )
    rows.append(
        TargetRow(
            "audit_events (total)",
            ctx.count(AuditEvent),
            None,
            None,
            "includes rows the running API appended",
        )
    )
    rows.extend(
        TargetRow(table, ctx.count(model), None, None, note)
        for table, model, note in _INFORMATIONAL
    )
    return rows, all(row.ok for row in rows)


def render(rows: list[TargetRow]) -> str:
    """Render the report as a fixed-width table."""
    width = max(len(row.table) for row in rows) + 2
    lines = [
        f"{'table'.ljust(width)}{'count'.rjust(7)}{'target'.rjust(10)}  status  note",
        "-" * (width + 7 + 10 + 10) + "-" * 40,
    ]
    for row in rows:
        status = "  -   " if not row.is_bounded else ("  ok  " if row.ok else " MISS ")
        lines.append(
            f"{row.table.ljust(width)}{row.count:>7}{row.target_label:>10}  {status}  {row.note}"
        )
    return "\n".join(lines)
