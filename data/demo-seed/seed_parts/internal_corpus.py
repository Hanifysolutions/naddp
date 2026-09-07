"""The mission's own documents: internal assessments and consular case notes.

WHY THIS EXISTS. Every document the seed loads from the citation registry is PUBLIC, by
construction -- a registry entry is a page anyone can open. A corpus of nothing but public
material makes the retrieval authorisation filter untestable: "a TRADE_OFFICER cannot see
consular material" is vacuously true when no consular material exists, and a vacuous pass
is worse than a failure because it looks like a control working.

So this seeds a small number of the mission's OWN documents:

* MISSION_INTERNAL assessments -- the analysis a mission writes about public signals. Land
  in the ``INTERNAL_NOTES`` collection, never mixed into a public-intelligence answer.
* CONSULAR_SENSITIVE case notes -- process notes attached to the consular caseload. These
  are the rows that must never surface for a role without the ``consular`` compartment,
  and they are what makes the Week 2 leakage test mean something.

Every row is synthetic (BUILD_BIBLE section 11). The consular notes describe process and
carry a ``DEMO-SUBJ`` token; they contain no passport number, no real citizen identifier,
and no narrative that would be sensitive if it were real. The classification is real even
though the content is invented -- that is the point of testing against them.

These documents deliberately have NO citation_id: they are the mission's own writing, not
a public source, so there is nothing for a reader to open. Retrieval surfaces them with
``evidence_id = None``, and any purpose that requires a resolving citation will correctly
decline to use them as evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain.enums import Classification, Jurisdiction, SourceType
from app.models.intelligence import Document, Source
from seed_parts.context import SeedContext, ulid_str
from seed_parts.objectstore import store_text

__all__ = ["seed_internal_corpus"]

#: The mission is its own source. Trust tier 1: we wrote it.
_MISSION_SOURCE_CODE = "naddp-mission-internal"


@dataclass(frozen=True)
class _InternalDoc:
    slug: str
    title: str
    classification: Classification
    body: str


_DOCS: tuple[_InternalDoc, ...] = (
    _InternalDoc(
        slug="assessment-lithium-midstream-posture",
        title="Mission assessment: Australian lithium midstream posture and what it implies",
        classification=Classification.MISSION_INTERNAL,
        body=(
            "Mission assessment, not for external distribution.\n\n"
            "The public record supports a narrow claim and not a broader one. Covalent's "
            "Kwinana lithium hydroxide refinery is ramping toward its existing nameplate "
            "capacity, and the Mt Holland concentrator is expanding its spodumene output. "
            "These are different assets at different points in the value chain, and the "
            "distinction matters in a room: as at September 2026 no Australian lithium "
            "refinery is expanding capacity, and stating otherwise would be checkable and "
            "wrong.\n\n"
            "What follows for the mission is that the credible opening is skills and "
            "training rather than plant investment. The processing-skills gap is "
            "independently documented by Australian industry bodies and by a Western "
            "Australian vocational assessment. That gap is the demand signal; it does not "
            "by itself imply any Nigerian counterparty.\n\n"
            "Officers should treat any Nigeria-Australia corridor proposition as an "
            "internal hypothesis pending qualification. No public source connects an "
            "Australian lithium operator to Nigeria, and the platform's own scoring "
            "reflects that by rating the corridor opportunity below every signal beneath "
            "it."
        ),
    ),
    _InternalDoc(
        slug="assessment-counterpart-engagement-sequencing",
        title="Mission assessment: sequencing counterpart engagement on the skills corridor",
        classification=Classification.MISSION_INTERNAL,
        body=(
            "Mission assessment, not for external distribution.\n\n"
            "Engagement sequencing on any training-corridor proposition should lead with "
            "the accreditation question rather than with volume. Nigeria's engineering "
            "regulator holds provisional Washington Accord signatory status, which is not "
            "the same as full signatory status and changes which skills-assessment pathway "
            "a Nigerian-trained engineer follows in Australia.\n\n"
            "Raising this first is a credibility move. A counterpart institution will know "
            "the distinction; a mission that does not will spend the rest of the meeting "
            "recovering. The pathway exists and is usable, but presenting it as "
            "frictionless would be a mistake officers cannot walk back."
        ),
    ),
    _InternalDoc(
        slug="consular-note-passport-renewal-process",
        title="Consular process note: student passport renewal handling",
        classification=Classification.CONSULAR_SENSITIVE,
        body=(
            "CONSULAR-SENSITIVE. Handling note, synthetic demo content.\n\n"
            "Student passport renewals arriving through the Canberra mission follow the "
            "standard documentary path. The SLA clock pauses while a case sits in "
            "AWAITING_CITIZEN, because delay attributable to the applicant must not count "
            "against the mission's service standard.\n\n"
            "Officers should confirm the applicant's study enrolment status before "
            "advancing a case to review, since an expired enrolment changes which "
            "supporting documents are required. Case DEMO-SUBJ-014 is the worked example "
            "used in officer induction.\n\n"
            "No determination may be recorded without a named officer. The system enforces "
            "this at the database level, not only in the application."
        ),
    ),
    _InternalDoc(
        slug="consular-note-welfare-escalation-thresholds",
        title="Consular process note: welfare escalation thresholds",
        classification=Classification.CONSULAR_SENSITIVE,
        body=(
            "CONSULAR-SENSITIVE. Handling note, synthetic demo content.\n\n"
            "Welfare matters escalate to the head of mission on two triggers: a case "
            "ageing past its SLA budget while still unassigned, or any matter involving "
            "detention. Escalation is a state transition and is audited like any other.\n\n"
            "Escalating early is preferred to escalating correctly. A case returned from "
            "escalation costs an officer an afternoon; a case that should have escalated "
            "and did not costs the mission its standing with the citizen."
        ),
    ),
)


def seed_internal_corpus(ctx: SeedContext) -> dict[str, Document]:
    """Create the mission's own source and its internal/consular documents."""
    source = ctx.upsert(
        Source,
        ctx.register("sources", _MISSION_SOURCE_CODE),
        code=_MISSION_SOURCE_CODE,
        name="Nigerian High Commission, Canberra - mission's own material",
        publisher="Nigerian High Commission, Canberra",
        source_type=SourceType.DIPLOMATIC_MISSION,
        jurisdiction=Jurisdiction.NG,
        base_url="",
        trust_tier=1,
        is_active=True,
        classification=Classification.MISSION_INTERNAL,
        last_ingested_at=ctx.now,
    )

    documents: dict[str, Document] = {}
    for spec in _DOCS:
        body = spec.body
        document_id = ctx.register("documents", f"internal:{spec.slug}")
        # Write the bytes, like every other seeded document. storage/README.md's contract
        # is that every object_uri resolves to a real file, and a document whose URI
        # dangles would fail tests/test_seed_integrity.py for good reason.
        stored = store_text("internal", ulid_str(document_id), f"{spec.slug}.md", body)
        documents[spec.slug] = ctx.upsert(
            Document,
            document_id,
            source_id=source.id,
            # The mission's own writing cites no public page, so there is nothing for a
            # reader to open. Retrieval surfaces these with evidence_id None, and a purpose
            # that requires a resolving citation will decline to use them as evidence.
            citation_id=None,
            title=spec.title,
            url="",
            object_uri=stored.object_uri,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            mime_type="text/markdown",
            byte_size=len(body.encode("utf-8")),
            published_at=ctx.now,
            retrieved_at=ctx.now,
            language="en",
            classification=spec.classification,
            summary=spec.title,
            full_text=body,
            doc_metadata={"origin": "mission", "slug": spec.slug, "synthetic": True},
        )

    ctx.session.flush()
    return documents
