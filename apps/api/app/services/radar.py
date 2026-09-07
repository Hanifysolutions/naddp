"""Opportunity Radar (Blueprint section 5, P0): signals -> dedupe -> classify -> candidates.

**The Radar proposes. It never creates.** BUILD_BIBLE section 6 lists commitments among the
acts that may never be autonomous, and an opportunity is the mission asserting that
something is worth its time. So :func:`scan` writes nothing at all - it reads signals,
suppresses duplicates, classifies them by sector and reports what a human might want to
promote. Promotion and linking are separate, permissioned, audited acts.

That split is the point. A radar that quietly created opportunities would produce a
pipeline nobody chose, and the first question a head of mission asks about a pipeline is
"who decided this mattered?".

**Authorisation before analysis.** Signals are filtered by the caller's clearance in SQL,
before anything is grouped or counted - so two officers legitimately see different radars,
and neither can infer the size of what they cannot read from a total that includes it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.domain.enums import PolicyResult, SignalStatus
from app.models.intelligence import Signal
from app.models.opportunities import Opportunity
from app.security.deps import readable_classifications
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "RadarCandidate",
    "RadarReport",
    "link_signals",
    "scan",
]

_logger = get_logger(__name__)

#: A signal older than this is context, not a lead. It still counts as evidence once an
#: opportunity exists; it just does not by itself argue for opening a new one.
_RECENT_WINDOW: Final[timedelta] = timedelta(days=90)

#: Below this a sector cluster is one story, not a pattern, and proposing an opportunity
#: from it would fill the pipeline with noise.
_MIN_CLUSTER: Final[int] = 2


@dataclass(frozen=True, slots=True)
class RadarCandidate:
    """A sector cluster that might be worth an opportunity, and the case for it."""

    sector_code: str
    signal_ids: tuple[uuid.UUID, ...]
    headlines: tuple[str, ...]
    citation_ids: tuple[str, ...]
    recent_count: int
    mean_confidence: float
    existing_opportunity_id: uuid.UUID | None
    rationale: str

    @property
    def is_new(self) -> bool:
        return self.existing_opportunity_id is None


@dataclass
class RadarReport:
    """One radar sweep: what was read, what was suppressed, what is proposed."""

    signals_visible: int = 0
    duplicates_suppressed: int = 0
    dismissed_skipped: int = 0
    unclassified: int = 0
    candidates: list[RadarCandidate] = field(default_factory=list)
    suppressed_examples: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        lines = [
            f"signals visible to this role : {self.signals_visible}",
            f"duplicates suppressed        : {self.duplicates_suppressed}",
            f"dismissed, skipped           : {self.dismissed_skipped}",
            f"no sector, unclassified      : {self.unclassified}",
            f"candidate clusters           : {len(self.candidates)}",
        ]
        for candidate in self.candidates:
            marker = "NEW" if candidate.is_new else "existing"
            lines.append(
                f"  [{marker:>8}] {candidate.sector_code:<22} "
                f"{len(candidate.signal_ids)} signal(s), "
                f"{len(candidate.citation_ids)} citation(s), "
                f"mean confidence {candidate.mean_confidence:.0f}"
            )
        return lines


def _sector_of(signal: Signal) -> str | None:
    """The sector a signal belongs to, normalised.

    A signal may carry several sector codes; the first is treated as primary. Deliberately
    simple and inspectable - this decides which cluster a signal joins, and a clustering
    rule an officer cannot follow is one they cannot correct.
    """
    for raw in signal.sectors or []:
        code = str(raw).strip().upper().replace("-", "_")
        if code:
            return code
    return None


def _citations_for(session: Session, signals: list[Signal]) -> list[str]:
    """The distinct VERIFIED citation ids behind a cluster, resolved through documents.

    Through ``documents.citation_id`` rather than any field on the signal: the document is
    what was actually ingested, and it is the row that carries the registry key a reader
    can open.
    """
    from app.models.intelligence import Document

    document_ids = [s.document_id for s in signals if s.document_id is not None]
    if not document_ids:
        return []
    rows = session.scalars(
        select(Document.citation_id).where(
            Document.id.in_(document_ids), Document.citation_id.is_not(None)
        )
    )
    return sorted({str(row) for row in rows if row})


def scan(session: Session, principal: Principal, *, now: datetime | None = None) -> RadarReport:
    """Read the signal set this principal may see and report candidate opportunities.

    Writes nothing. Every number here is reproducible from the same rows.
    """
    now = now or datetime.now(UTC)
    report = RadarReport()

    # Authorisation first, in SQL, before anything is counted or grouped.
    visible = list(
        session.scalars(
            select(Signal)
            .where(Signal.classification.in_(readable_classifications(principal)))
            .order_by(Signal.detected_at.desc(), Signal.id)
        )
    )
    report.signals_visible = len(visible)

    seen_dedupe: dict[str, uuid.UUID] = {}
    clusters: dict[str, list[Signal]] = defaultdict(list)

    for signal in visible:
        if signal.status is SignalStatus.DISMISSED:
            report.dismissed_skipped += 1
            continue

        # Dedupe on the key the ingester computed. Two feeds carrying the same story is
        # the normal case, not the exception, and counting it twice would inflate every
        # cluster that matters most.
        key = (signal.dedupe_key or "").strip()
        if key:
            first = seen_dedupe.get(key)
            if first is not None:
                report.duplicates_suppressed += 1
                if len(report.suppressed_examples) < 5:
                    report.suppressed_examples.append(f"{signal.title[:60]} (dedupe_key={key})")
                continue
            seen_dedupe[key] = signal.id

        sector = _sector_of(signal)
        if sector is None:
            report.unclassified += 1
            continue
        clusters[sector].append(signal)

    existing = {
        str(row.sector_code or "").strip().upper().replace("-", "_"): row.id
        for row in session.scalars(select(Opportunity))
    }

    for sector, signals in sorted(clusters.items()):
        if len(signals) < _MIN_CLUSTER:
            continue
        recent = [s for s in signals if (now - s.detected_at) <= _RECENT_WINDOW]
        confidences = [float(s.confidence) for s in signals if s.confidence is not None]
        citations = _citations_for(session, signals)
        opportunity_id = existing.get(sector)
        report.candidates.append(
            RadarCandidate(
                sector_code=sector,
                signal_ids=tuple(s.id for s in signals),
                headlines=tuple(s.title for s in signals[:4]),
                citation_ids=tuple(citations[:8]),
                recent_count=len(recent),
                mean_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
                existing_opportunity_id=opportunity_id,
                rationale=(
                    f"{len(signals)} distinct signal(s) in {sector}, {len(recent)} of them "
                    f"inside the last {_RECENT_WINDOW.days} days"
                    + (
                        "; already carried by an existing opportunity"
                        if opportunity_id
                        else "; no opportunity covers this yet"
                    )
                ),
            )
        )

    _logger.info(
        "radar.scan",
        actor_role=principal.role.value,
        visible=report.signals_visible,
        duplicates=report.duplicates_suppressed,
        candidates=len(report.candidates),
    )
    return report


def link_signals(
    session: Session,
    principal: Principal,
    opportunity_id: uuid.UUID,
    signal_ids: Sequence[uuid.UUID],
    *,
    reason: str,
) -> int:
    """Attach signals to an opportunity as its evidence base. Audited.

    Separate from :func:`scan` on purpose. Linking changes what an opportunity is *made
    of*, and therefore its score, so it is a decision a named person takes rather than
    something a sweep does on its own.
    """
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")

    if not principal.has(Permission.QUALIFY_OPPORTUNITY):
        detail = (
            f"{principal.role.value} does not hold "
            f"{Permission.QUALIFY_OPPORTUNITY.value}, which linking evidence requires."
        )
        write_audit_event(
            session,
            actor=principal,
            action="opportunity.link_signals.denied",
            object_type="opportunities.opportunity",
            object_id=opportunity.id,
            policy_result=PolicyResult.DENY,
            classification=opportunity.classification,
            summary=detail,
            payload={"signal_count": len(signal_ids)},
        )
        raise PermissionDeniedError(detail)

    readable = readable_classifications(principal)
    signals = list(
        session.scalars(
            select(Signal).where(
                Signal.id.in_(list(signal_ids)),
                # A caller cannot attach evidence they are not cleared to read: doing so
                # would let a lower-cleared reader infer its content from the score.
                Signal.classification.in_(readable),
            )
        )
    )

    linked = 0
    for signal in signals:
        if signal.opportunity_id == opportunity.id:
            continue
        signal.opportunity_id = opportunity.id
        if signal.status is SignalStatus.NEW:
            signal.status = SignalStatus.LINKED
        linked += 1
    session.flush()

    if linked:
        write_audit_event(
            session,
            actor=principal,
            action="opportunity.signals_linked",
            object_type="opportunities.opportunity",
            object_id=opportunity.id,
            policy_result=PolicyResult.ALLOW,
            classification=opportunity.classification,
            summary=(
                f"{principal.full_name} ({principal.role.value}) linked {linked} signal(s) "
                f"to {opportunity.title!r} as supporting evidence. Reason: {reason}"
            ),
            payload={
                "linked": linked,
                "requested": len(signal_ids),
                "not_visible_to_actor": len(signal_ids) - len(signals),
                "reason": reason,
            },
        )
    return linked


def candidate_payload(report: RadarReport) -> list[dict[str, Any]]:
    """The report as plain data, for an API response or a trace."""
    return [
        {
            "sector_code": candidate.sector_code,
            "is_new": candidate.is_new,
            "signal_count": len(candidate.signal_ids),
            "recent_count": candidate.recent_count,
            "mean_confidence": round(candidate.mean_confidence, 1),
            "headlines": list(candidate.headlines),
            "rationale": candidate.rationale,
            "existing_opportunity_id": (
                str(candidate.existing_opportunity_id)
                if candidate.existing_opportunity_id
                else None
            ),
        }
        for candidate in report.candidates
    ]
