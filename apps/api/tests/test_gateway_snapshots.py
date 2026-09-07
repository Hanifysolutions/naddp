"""The deterministic snapshot set: coverage, schema validity, and citation integrity.

ADR-0002 lists these as CI checks rather than conventions, because a snapshot that rots
against the seed does not fail loudly -- it fails on stage. Three of the four checks in the
ADR's enforcement table live here; the fourth (a snapshot citing evidence outside the
caller's authorised set is refused) is in ``test_gateway_pipeline.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from app.ai.evidence import citation_registry, unverified_ids
from app.ai.fallback import clear_snapshot_cache, load_snapshot, snapshot_file, snapshot_inventory
from app.ai.gateway import generate_traced
from app.ai.purposes import DEFAULT_SCENARIO, PURPOSES
from app.ai.schemas import GatewayContext
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode
from app.security.principal import principal_for_role

PURPOSE_LIST = list(PURPOSES)

#: One representative caller per purpose, plus the scenario the demo script exercises.
DEMO_BEATS: dict[AiPurpose, tuple[RoleCode, str | None]] = {
    AiPurpose.MORNING_BRIEF: (RoleCode.AMBASSADOR, None),
    AiPurpose.OPPORTUNITY_SCORE: (RoleCode.TRADE_OFFICER, "au-lithium-ng-skills-corridor"),
    AiPurpose.MEETING_PREP: (RoleCode.TRADE_OFFICER, "covalent-lithium-bilateral"),
    AiPurpose.MEETING_FOLLOWUP: (RoleCode.TRADE_OFFICER, "covalent-lithium-bilateral"),
    AiPurpose.CONSULAR_TRIAGE: (RoleCode.CONSULAR_OFFICER, "passport-renewal"),
    AiPurpose.KNOWLEDGE_ANSWER: (RoleCode.TRADE_OFFICER, "lithium-processing-skills-pathways"),
    AiPurpose.DIASPORA_MATCH: (RoleCode.DIASPORA_OFFICER, "lithium-migration"),
}

#: The four fact keys the CONSULAR_TRIAGE context policy admits in these fixtures.
TRIAGE_FACTS: Mapping[str, str | int | float | bool | None] = {
    "case_type": "PASSPORT_RENEWAL",
    "case_age_days": 16,
    "sla_state": "RUNNING",
    "status": "ASSIGNED",
}


@pytest.fixture(autouse=True)
def _clean_snapshot_cache() -> Iterator[None]:
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


def _snapshot_files() -> list[Path]:
    files = sorted(snapshot_inventory().values())
    assert files, "no snapshots on disk: the demo would dead-end on every AI call"
    return files


def _documents() -> list[tuple[Path, dict[str, object]]]:
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in _snapshot_files()]


def _text_of(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False)


# --------------------------------------------------------------------------- coverage


@pytest.mark.parametrize("purpose", PURPOSE_LIST)
def test_every_purpose_has_a_default_snapshot(purpose: AiPurpose) -> None:
    """ADR-0002: the ``__default__`` scenario is the harness's last resort before BLOCKED."""
    path = snapshot_file(purpose, DEFAULT_SCENARIO)
    assert path.is_file(), f"missing {path.name}"


@pytest.mark.parametrize("purpose", PURPOSE_LIST)
def test_every_purpose_has_at_least_one_named_scenario(purpose: AiPurpose) -> None:
    prefix = f"{purpose.value.lower()}_"
    named = [
        stem
        for stem in snapshot_inventory()
        if stem.startswith(prefix) and not stem.endswith(DEFAULT_SCENARIO)
    ]
    assert named, f"{purpose.value} has only a default snapshot; no hero beat is covered"


@pytest.mark.parametrize("purpose", PURPOSE_LIST)
def test_every_default_snapshot_loads_and_validates(purpose: AiPurpose) -> None:
    snapshot = load_snapshot(PURPOSES[purpose], DEFAULT_SCENARIO)
    assert snapshot is not None
    assert snapshot.purpose is purpose
    assert isinstance(snapshot.result, PURPOSES[purpose].output_schema)


def test_every_snapshot_on_disk_belongs_to_a_registered_purpose() -> None:
    known = {purpose.value for purpose in AiPurpose}
    for path, document in _documents():
        assert document.get("purpose") in known, path.name


def test_every_snapshot_on_disk_validates_against_its_purpose_schema() -> None:
    for path, document in _documents():
        purpose = AiPurpose(str(document["purpose"]))
        spec = PURPOSES[purpose]
        scenario = str(document["scenario"])
        assert path.stem == f"{purpose.value.lower()}_{scenario}", (
            f"{path.name} declares scenario {scenario!r}, which does not match its filename; "
            "the harness looks a snapshot up by filename, so it would never be served"
        )
        snapshot = load_snapshot(spec, scenario)
        assert snapshot is not None, f"{path.name} did not load"
        assert isinstance(snapshot.result, spec.output_schema)


def test_every_snapshot_declares_the_approval_status_its_purpose_assigns() -> None:
    for path, document in _documents():
        spec = PURPOSES[AiPurpose(str(document["purpose"]))]
        assert document["approval_status"] == spec.approval_status.value, path.name


# --------------------------------------------------------------------------- citations


def test_every_snapshot_cites_only_verified_registry_ids() -> None:
    """A snapshot citing a TODO_VERIFY or non-existent id is a demo-breaking bug.

    ``citations.json`` rule 2: "The seed may cite ONLY entries whose verification.status is
    VERIFIED. A TODO_VERIFY entry is research output, not demo content." A TODO_VERIFY URL
    may 404 on stage, which destroys winning moment #1 in the one place it is being made.
    """
    for path, document in _documents():
        spec = PURPOSES[AiPurpose(str(document["purpose"]))]
        snapshot = load_snapshot(spec, str(document["scenario"]))
        assert snapshot is not None
        cited = snapshot.result.cited_evidence_ids()
        assert cited, f"{path.name} cites nothing"
        offenders = unverified_ids(cited)
        assert not offenders, f"{path.name} cites non-VERIFIED or unknown ids: {offenders}"


def test_declared_evidence_ids_match_what_the_result_actually_cites() -> None:
    """The two must agree or the trace drawer lists sources the answer never used."""
    for path, document in _documents():
        spec = PURPOSES[AiPurpose(str(document["purpose"]))]
        snapshot = load_snapshot(spec, str(document["scenario"]))
        assert snapshot is not None
        assert set(snapshot.evidence_ids) == snapshot.result.cited_evidence_ids(), path.name


def test_every_cited_source_has_a_resolvable_url() -> None:
    registry = citation_registry()
    for path, document in _documents():
        spec = PURPOSES[AiPurpose(str(document["purpose"]))]
        snapshot = load_snapshot(spec, str(document["scenario"]))
        assert snapshot is not None
        for identifier in snapshot.result.cited_evidence_ids():
            entry = registry[identifier]
            assert entry.url.startswith("http"), f"{path.name}: {identifier} has no URL"
            assert entry.title, f"{path.name}: {identifier} has no title"


# --------------------------------------------------------------------------- end to end


@pytest.mark.parametrize("purpose", PURPOSE_LIST)
def test_each_purpose_returns_a_grounded_answer_with_the_live_path_disabled(
    purpose: AiPurpose,
) -> None:
    """PROMPT_W1 VERIFY item 5: every purpose answers offline, with ``fallback=true``."""
    role, subject = DEMO_BEATS[purpose]
    facts = TRIAGE_FACTS if purpose is AiPurpose.CONSULAR_TRIAGE else {}
    context = GatewayContext(subject_ref=subject, facts=facts)

    outcome = generate_traced(
        purpose,
        Classification.MISSION_INTERNAL,
        context,
        principal_for_role(role),
    )

    assert outcome.envelope.result is not None, outcome.envelope.explanation
    assert outcome.envelope.approval_status is PURPOSES[purpose].approval_status
    assert outcome.envelope.evidence
    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == "LIVE_DISABLED"
    assert outcome.trace.citation_check_passed is True
    assert outcome.trace.approval_status is not ApprovalStatus.BLOCKED
    assert len(outcome.trace.stages) == 9


@pytest.mark.parametrize("role", list(RoleCode))
def test_the_morning_brief_answers_for_every_persona(role: RoleCode) -> None:
    """The role picker must not be able to reach a dead end on the first screen."""
    outcome = generate_traced(
        AiPurpose.MORNING_BRIEF,
        Classification.PUBLIC,
        GatewayContext(),
        principal_for_role(role),
    )
    assert outcome.envelope.result is not None
    assert outcome.envelope.evidence


# --------------------------------------------------------------------------- hero facts


@pytest.mark.parametrize(
    "forbidden",
    [
        "refinery expansion",
        "expanding refinery",
        "refinery is expanding",
        "expanding its refinery",
        "expansion of the refinery",
        "expanding the kwinana refinery",
        "refinery expansion project",
    ],
)
def test_no_snapshot_says_an_australian_lithium_refinery_is_expanding(forbidden: str) -> None:
    """OPEN_QUESTIONS Q-16. Concentrator and refinery are different assets.

    As at September 2026 Australian lithium refining capacity is not growing: Albemarle's
    Kemerton is in care and maintenance and Tianqi halted Kwinana Phase 2. The July 2026
    Mt Holland approval doubles CONCENTRATE production. Conflating the two is a factual
    error that would destroy winning moment #1 in the moment it is being made.

    The ban is on the PHRASE and applies even inside a correct denial. Two reasons, and
    both are deliberate: a substring test that has to understand negation is a test that
    will eventually get the negation wrong, and a sentence in a snapshot is display copy --
    it gets read aloud, screenshotted and pasted into a slide, where the "no" in front of it
    does not necessarily travel with it. Write the point positively instead; the current
    snapshots say "Australia's lithium refining capacity is not growing" and name the two
    plants, which is stronger copy anyway.
    """
    for path, document in _documents():
        assert forbidden not in _text_of(document).lower(), path.name


def test_the_corridor_is_always_labelled_as_ai_proposed() -> None:
    """OPEN_QUESTIONS Q-17. No public source links an Australian lithium operator to Nigeria."""
    markers = ("ai-proposed", "no public source", "platform proposes", "is not documented")
    for path, document in _documents():
        text = _text_of(document).lower()
        mentions_corridor = "corridor" in text or "is_proposed_by_ai" in text
        if not mentions_corridor:
            continue
        assert any(marker in text for marker in markers), (
            f"{path.name} discusses the corridor without saying it is AI-proposed"
        )


def test_the_scored_opportunity_is_flagged_and_less_confident_than_its_drivers() -> None:
    for purpose_scenario in ("__default__", "au-lithium-ng-skills-corridor"):
        snapshot = load_snapshot(PURPOSES[AiPurpose.OPPORTUNITY_SCORE], purpose_scenario)
        assert snapshot is not None
        payload = snapshot.result.model_dump()
        assert payload["is_proposed_by_ai"] is True
        assert payload["confidence"] < max(driver["weight"] for driver in payload["drivers"])


def test_the_consular_snapshots_never_claim_to_have_read_narrative() -> None:
    for scenario in ("__default__", "passport-renewal"):
        snapshot = load_snapshot(PURPOSES[AiPurpose.CONSULAR_TRIAGE], scenario)
        assert snapshot is not None
        payload = snapshot.result.model_dump()
        assert payload["narrative_withheld"] is True
        assert payload["requires_human_determination"] is True


# --------------------------------------------------------------------------- safety rails


def test_no_snapshot_contains_an_email_address() -> None:
    """BUILD_BIBLE section 11: no real private contact details anywhere in the demo."""
    for path, document in _documents():
        text = _text_of(document)
        assert "@" not in text.replace("@naddp.demo", ""), (
            f"{path.name} contains an '@', which in a drafted communication is one careless "
            "click from being a real email"
        )


def test_no_snapshot_contains_a_passport_number_shaped_string() -> None:
    """A synthetic case must not carry anything that reads as an identity document number."""
    import re

    pattern = re.compile(r"\b[A-Z]{1,2}\d{7,9}\b")
    for path, document in _documents():
        assert not pattern.search(_text_of(document)), path.name
