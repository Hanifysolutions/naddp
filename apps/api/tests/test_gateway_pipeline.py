"""The nine-stage pipeline: refusals, the fallback harness, and the trace it writes.

Every test here runs with no database and no API key. That is the point of ADR-0002: the
deterministic path is a first-class state, so the whole pipeline is exercisable offline.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.ai import fallback as fallback_module
from app.ai import gateway as gateway_module
from app.ai.evidence import authorise_evidence
from app.ai.fallback import (
    BudgetExpiredError,
    FallbackReason,
    call_with_budget,
    clear_snapshot_cache,
    load_snapshot,
    resolve_snapshot,
    snapshot_file,
)
from app.ai.gateway import _ProviderResponse, generate, generate_traced
from app.ai.purposes import DEFAULT_SCENARIO, HEAVY_BUDGET_SECONDS, PURPOSES, PurposeNotAllowedError
from app.ai.schemas import GatewayContext, GatewayResult
from app.core import config as config_module
from app.core.config import Settings
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode
from app.domain.grounding import ArticleSupport, GroundingSource, KnowledgeGrounding
from app.models.ai import FALLBACK_REASONS
from app.security.principal import principal_for_role

#: A real registry entry whose verification.status is TODO_VERIFY. Research output, not
#: demo content -- the registry's own rule 2 -- so citing it must fail the post-check.
UNVERIFIED_ID = "disr-critical-minerals-strategy-2023-2030"

TRIAGE_FACTS: dict[str, Any] = {
    "case_type": "PASSPORT_RENEWAL",
    "case_age_days": 16,
    "sla_state": "RUNNING",
    "status": "ASSIGNED",
}


@pytest.fixture(autouse=True)
def _clean_snapshot_cache() -> Iterator[None]:
    """A cached snapshot (or cached miss) must never leak between tests."""
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


def _live_settings(**overrides: Any) -> Settings:
    """Settings with the live path switched on, for exercising the harness."""
    values: dict[str, Any] = {
        "app_env": "test",
        "ai_gateway_live": True,
        "anthropic_api_key": "sk-ant-not-a-real-key",
        "ai_gateway_timeout_seconds": 0.25,
    }
    values.update(overrides)
    return Settings(**values)


def _go(
    purpose: AiPurpose,
    *,
    role: RoleCode = RoleCode.TRADE_OFFICER,
    data_class: Classification = Classification.MISSION_INTERNAL,
    context: GatewayContext | None = None,
) -> gateway_module.GatewayOutcome:
    return generate_traced(
        purpose,
        data_class,
        context if context is not None else GatewayContext(),
        principal_for_role(role),
    )


def _stage(outcome: gateway_module.GatewayOutcome, name: str) -> gateway_module.StageRecord:
    matches = [record for record in outcome.trace.stages if record.stage == name]
    assert matches, f"no {name} stage recorded; got {[s.stage for s in outcome.trace.stages]}"
    return matches[-1]


# --------------------------------------------------------------------------- happy path


def test_deterministic_path_serves_a_grounded_answer() -> None:
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    envelope = outcome.envelope

    assert envelope.approval_status is ApprovalStatus.NOT_REQUIRED
    assert envelope.result is not None
    assert envelope.evidence, "winning moment #1: an answer with no evidence is not shippable"
    assert envelope.trace_id == str(outcome.trace.trace_id)
    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == FallbackReason.LIVE_DISABLED.value
    assert outcome.trace.live is False


def test_every_call_records_all_nine_stages() -> None:
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert [record.stage for record in outcome.trace.stages] == [
        "purpose_allowlist",
        "classification_gate",
        "retrieval_authorisation",
        "context_assembly",
        "model_route",
        "generation",
        "schema_validation",
        "citation_post_check",
        "trace_write",
    ]
    for record in outcome.trace.stages:
        assert record.detail, f"stage {record.stage} recorded no detail"
        assert record.ms >= 0


def test_trace_records_the_routing_decision_and_the_reason() -> None:
    """BUILD_BIBLE section 5: the routing decision must be visible, not merely taken."""
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.trace.model_route == "external-noret"
    assert len(outcome.trace.route_reason) > 40
    # Q-12 is resolved; the reason now cites the section 4a table it applies.
    assert "Section 4a" in outcome.trace.route_reason


def test_trace_records_the_retrieval_filter_that_ran_before_selection() -> None:
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    recorded = outcome.trace.retrieval_filter
    assert recorded["applied"] == "before_selection"
    assert recorded["verified_only"] is True
    assert recorded["actor_role"] == "AMBASSADOR"
    assert "PUBLIC" in recorded["classifications"]


def test_generate_returns_only_the_envelope() -> None:
    result = generate(
        AiPurpose.MORNING_BRIEF,
        Classification.MISSION_INTERNAL,
        GatewayContext(),
        principal_for_role(RoleCode.AMBASSADOR),
    )
    assert isinstance(result, GatewayResult)


def test_consequential_purpose_blocks_on_approval_even_from_a_snapshot() -> None:
    """Winning moment #2. A cached draft is still a draft."""
    outcome = _go(
        AiPurpose.MEETING_FOLLOWUP,
        context=GatewayContext(subject_ref="covalent-lithium-bilateral"),
    )
    assert outcome.envelope.approval_status is ApprovalStatus.PENDING_APPROVAL
    assert outcome.trace.fallback is True


def test_unknown_scenario_falls_back_to_the_default_snapshot() -> None:
    outcome = _go(
        AiPurpose.OPPORTUNITY_SCORE,
        context=GatewayContext(subject_ref="an-opportunity-with-no-snapshot"),
    )
    assert outcome.trace.scenario == "an-opportunity-with-no-snapshot"
    assert outcome.trace.snapshot_key == f"opportunity_score_{DEFAULT_SCENARIO}"
    assert outcome.envelope.result is not None


# --------------------------------------------------------------------------- stage 1


def test_unregistered_purpose_raises_rather_than_returning_an_envelope() -> None:
    """There is no trace row and no snapshot for a purpose that does not exist."""
    with pytest.raises(PurposeNotAllowedError):
        generate_traced(
            "GENERAL_CHAT",  # type: ignore[arg-type]
            Classification.PUBLIC,
            GatewayContext(),
            principal_for_role(RoleCode.AMBASSADOR),
        )


# --------------------------------------------------------------------------- stage 2


def test_caller_without_clearance_is_refused() -> None:
    """ADMIN holds rank 10 and no compartment: it may not generate over CONFIDENTIAL."""
    outcome = _go(
        AiPurpose.OPPORTUNITY_SCORE,
        role=RoleCode.ADMIN,
        data_class=Classification.CONFIDENTIAL,
    )
    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    assert outcome.envelope.result is None
    assert "not cleared" in (outcome.envelope.explanation or "")
    assert _stage(outcome, "classification_gate").ok is False
    assert outcome.trace.live is False and outcome.trace.fallback is False


def test_consular_sensitive_is_refused_even_for_a_cleared_officer() -> None:
    """Q-06 conservative reading: only triage may be declared over CONSULAR_SENSITIVE.

    The deputy HOLDS the consular compartment and may read the zone. The refusal is the
    purpose's ceiling, not the caller's clearance -- which is exactly the control that keeps
    case narrative away from a third-party model.
    """
    principal = principal_for_role(RoleCode.DEPUTY)
    assert principal.may_read(Classification.CONSULAR_SENSITIVE)

    outcome = _go(
        AiPurpose.MEETING_PREP,
        role=RoleCode.DEPUTY,
        data_class=Classification.CONSULAR_SENSITIVE,
    )
    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    assert outcome.envelope.result is None
    assert "Q-06" in (outcome.envelope.explanation or "")
    assert _stage(outcome, "classification_gate").ok is False


#: The six metadata fields the triage route derives from an urgent case's row and clock.
URGENT_TRIAGE_FACTS: dict[str, Any] = {
    "case_type": "EMERGENCY_TRAVEL_DOCUMENT",
    "case_age_days": 1.8,
    "sla_state": "DUE_SOON",
    "sla_days_remaining": 0.2,
    "days_paused": 0.0,
    "status": "NEW",
}


def test_consular_triage_over_consular_sensitive_asks_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W3.3: the CONSULAR-SENSITIVE band answers triage by metadata rules, with no model call.

    The live path is switched ON and the provider replaced by a tripwire, so the only way this
    passes is if the route -- not the configuration -- is what kept the call from happening.
    """
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)

    def _tripwire(**_: Any) -> _ProviderResponse:
        raise AssertionError("a CONSULAR_SENSITIVE triage must never reach a model")

    monkeypatch.setattr(gateway_module, "_call_provider", _tripwire)

    outcome = _go(
        AiPurpose.CONSULAR_TRIAGE,
        role=RoleCode.CONSULAR_OFFICER,
        data_class=Classification.CONSULAR_SENSITIVE,
        context=GatewayContext(subject_ref="NADDP-TESTREF", facts=URGENT_TRIAGE_FACTS),
    )
    trace = outcome.trace
    assert outcome.envelope.approval_status is ApprovalStatus.PENDING_APPROVAL
    assert trace.model_route == "no-external-model"
    assert trace.route_badge == (
        "CONSULAR-SENSITIVE · no external route · metadata-only · generation withheld"
    )
    assert trace.model_requested is None and trace.model_used is None
    assert trace.live is False
    assert trace.fallback is False, "a metadata-rules answer is not a fallback"
    assert trace.snapshot_key is None
    assert _stage(outcome, "generation").ok is True
    assert "No model was asked" in _stage(outcome, "generation").detail

    result = outcome.envelope.result
    assert result is not None
    payload = result.model_dump()
    assert payload["proposed_priority"] == "URGENT"
    assert payload["narrative_withheld"] is True
    assert payload["requires_human_determination"] is True
    assert set(payload["inputs_used"]) <= set(URGENT_TRIAGE_FACTS)
    assert outcome.envelope.evidence, "the proposal still cites real published guidance"


def test_a_purpose_capped_below_confidential_refuses_confidential() -> None:
    outcome = _go(
        AiPurpose.MEETING_FOLLOWUP,
        role=RoleCode.AMBASSADOR,
        data_class=Classification.CONFIDENTIAL,
    )
    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    assert "MEETING_FOLLOWUP may process" in (outcome.envelope.explanation or "")


# --------------------------------------------------------------------------- stage 4


def test_consular_triage_refuses_case_narrative() -> None:
    """The context builder rejects narrative, and the refusal is recorded in the trace."""
    outcome = _go(
        AiPurpose.CONSULAR_TRIAGE,
        role=RoleCode.CONSULAR_OFFICER,
        context=GatewayContext(
            facts={**TRIAGE_FACTS, "case_narrative": "The applicant reported that ..."}
        ),
    )
    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    assert outcome.envelope.result is None

    stage = _stage(outcome, "context_assembly")
    assert stage.ok is False
    assert "field_not_allowlisted" in stage.detail
    assert "case_narrative" in stage.detail
    assert outcome.trace.error is not None and "case_narrative" in outcome.trace.error
    # The refusal is not an availability failure, so no snapshot is served in its place.
    assert outcome.trace.fallback is False


def test_consular_triage_narrative_refusal_happens_before_any_prompt_is_built() -> None:
    outcome = _go(
        AiPurpose.CONSULAR_TRIAGE,
        role=RoleCode.CONSULAR_OFFICER,
        context=GatewayContext(facts={"case_narrative": "..."}),
    )
    assert outcome.trace.prompt_hash is None
    assert [record.stage for record in outcome.trace.stages] == [
        "purpose_allowlist",
        "classification_gate",
        "retrieval_authorisation",
        "context_assembly",
        "trace_write",
    ]


def test_consular_triage_accepts_metadata_and_proposes_a_triage() -> None:
    outcome = _go(
        AiPurpose.CONSULAR_TRIAGE,
        role=RoleCode.CONSULAR_OFFICER,
        context=GatewayContext(subject_ref="passport-renewal", facts=TRIAGE_FACTS),
    )
    assert outcome.envelope.approval_status is ApprovalStatus.PENDING_APPROVAL
    result = outcome.envelope.result
    assert result is not None
    payload = result.model_dump()
    assert payload["narrative_withheld"] is True
    assert payload["requires_human_determination"] is True
    assert set(payload["inputs_used"]) <= set(TRIAGE_FACTS)


# --------------------------------------------------------------------------- the harness


def test_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)
    # The budget is PER PURPOSE now, so AI_GATEWAY_TIMEOUT_SECONDS no longer governs
    # morning_brief (it gets 25s). Patch the resolver rather than the setting: the property
    # under test is "the budget bounds the response", not "the budget is 0.2s", and waiting
    # 25s to prove it would be a poor trade.
    monkeypatch.setattr(gateway_module, "budget_for", lambda _spec: 0.2)

    def _slow(**_: Any) -> _ProviderResponse:
        time.sleep(1.5)
        raise AssertionError("the harness should have abandoned this call")

    monkeypatch.setattr(gateway_module, "_call_provider", _slow)

    started = time.perf_counter()
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    elapsed = time.perf_counter() - started

    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == FallbackReason.TIMEOUT.value
    assert outcome.trace.live is True, "a call WAS attempted; the trace must say so"
    assert outcome.envelope.result is not None, "the demo must not dead-end on a timeout"
    assert elapsed < 1.4, "the budget must bound the response, not the provider"
    assert outcome.trace.budget_seconds == pytest.approx(0.2), (
        "the trace must record the budget the call was actually given"
    )


def test_the_budget_is_per_purpose_and_recorded() -> None:
    """Architect ruling, W2.2 review: a flat 4s made every heavy call time out."""
    heavy = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    light = _go(AiPurpose.OPPORTUNITY_SCORE, role=RoleCode.AMBASSADOR)

    assert heavy.trace.budget_seconds is not None, "the trace must record the budget it was given"
    assert light.trace.budget_seconds is not None, "the trace must record the budget it was given"
    assert heavy.trace.budget_seconds == pytest.approx(HEAVY_BUDGET_SECONDS)
    assert light.trace.budget_seconds == pytest.approx(4.0)
    assert heavy.trace.budget_seconds > light.trace.budget_seconds


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (RuntimeError("connection reset"), FallbackReason.API_ERROR),
        (ValueError("malformed response"), FallbackReason.API_ERROR),
    ],
)
def test_api_error_falls_back(
    monkeypatch: pytest.MonkeyPatch, exception: Exception, expected: FallbackReason
) -> None:
    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    def _boom(**_: Any) -> _ProviderResponse:
        raise exception

    monkeypatch.setattr(gateway_module, "_call_provider", _boom)

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == expected.value
    assert outcome.envelope.result is not None


def test_rate_limit_is_classified_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason is matched on the SDK class name, so the SDK need not be imported."""

    class RateLimitError(Exception):
        pass

    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    def _throttled(**_: Any) -> _ProviderResponse:
        raise RateLimitError("429")

    monkeypatch.setattr(gateway_module, "_call_provider", _throttled)

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.trace.fallback_reason == FallbackReason.RATE_LIMIT.value


def test_schema_invalid_response_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    def _garbage(**_: Any) -> _ProviderResponse:
        return _ProviderResponse(
            payload={"not": "a morning brief"},
            model_used="claude-test",
            input_tokens=10,
            output_tokens=5,
        )

    monkeypatch.setattr(gateway_module, "_call_provider", _garbage)

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.trace.schema_valid is False
    assert outcome.trace.fallback_reason == FallbackReason.SCHEMA_INVALID.value
    assert outcome.envelope.result is not None


def test_missing_key_falls_back_without_attempting_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _live_settings(anthropic_api_key=None)
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    def _never(**_: Any) -> _ProviderResponse:
        raise AssertionError("no call may be attempted without a credential")

    monkeypatch.setattr(gateway_module, "_call_provider", _never)

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.trace.live is False
    assert outcome.trace.fallback_reason == FallbackReason.NO_API_KEY.value
    assert outcome.envelope.result is not None


def test_fallback_reasons_match_the_trace_column_vocabulary() -> None:
    assert {reason.value for reason in FallbackReason} == set(FALLBACK_REASONS)


def test_call_with_budget_reraises_the_operation_error_unchanged() -> None:
    def _boom() -> str:
        raise KeyError("upstream")

    with pytest.raises(KeyError):
        call_with_budget(_boom, seconds=2.0)


def test_call_with_budget_raises_on_expiry() -> None:
    def _slow() -> str:
        time.sleep(1.0)
        return "late"

    with pytest.raises(BudgetExpiredError):
        call_with_budget(_slow, seconds=0.1)


# --------------------------------------------------------------------------- stage 8

#: A VERIFIED registry id a TRADE_OFFICER may be shown: the hero knowledge article's citation.
GROUNDED_CITATION = "mriwa-fbicrc-battery-vocational-skills-gap-plan"


def _supported_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for stage 3's database work: one approved article supports the question.

    KNOWLEDGE_ANSWER grounds in the approved knowledge base or refuses (A-17), and the real
    grounding needs Postgres. These stage 8 tests are about what happens to a live answer that
    cites badly, so they substitute a supported grounding and keep everything else real.
    """
    text = "A synthetic approved sentence about lithium processing skills and pathways."
    source = GroundingSource(
        article_id=uuid.uuid4(),
        slug="lithium-processing-skills-pathways",
        title="Lithium processing skills and the pathways into them",
        version=3,
        category="critical-minerals",
        citation_id=GROUNDED_CITATION,
        classification=Classification.PUBLIC,
        approved_by_name="Tunde Bakare",
        approved_at=None,
        valid_until=None,
        coverage=0.8,
        matched_terms=("lithium", "skill"),
        passage=(text,),
        full_text=text,
    )
    grounding = KnowledgeGrounding(
        consulted=True,
        question_terms=("lithium", "skill"),
        ignored_terms=(),
        eligible_count=1,
        candidates=(
            ArticleSupport(
                slug=source.slug,
                coverage=0.8,
                matched_terms=source.matched_terms,
                retrieval_rank=1,
                citable=True,
            ),
        ),
        sources=(source,),
    )
    monkeypatch.setattr(gateway_module, "_ground_knowledge", lambda *_a, **_k: grounding)


KNOWLEDGE_QUESTION = GatewayContext(question="What lithium processing skills are there?")


def test_live_answer_citing_an_unverified_id_is_refused_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TODO_VERIFY id is research output, not demo content (citations.json rule 2)."""
    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    payload = {
        "question": "Does Australia have a critical minerals strategy?",
        "answer": "Yes, and here is a citation that has not been verified.",
        "citations": [UNVERIFIED_ID],
        "caveats": [],
        "answered_from_approved_sources": True,
        "confidence": 0.9,
    }

    def _hallucinating(**_: Any) -> _ProviderResponse:
        return _ProviderResponse(
            payload=payload, model_used="claude-test", input_tokens=1, output_tokens=1
        )

    monkeypatch.setattr(gateway_module, "_call_provider", _hallucinating)

    _supported_grounding(monkeypatch)
    outcome = _go(AiPurpose.KNOWLEDGE_ANSWER, context=KNOWLEDGE_QUESTION)
    assert outcome.trace.fallback_reason == FallbackReason.CITATION_CHECK_FAILED.value
    assert outcome.trace.citation_check_passed is True, "the approved text passed the re-check"
    assert outcome.trace.snapshot_key is None, "a knowledge answer never falls back to a snapshot"
    assert outcome.trace.evidence_ids == [GROUNDED_CITATION]
    assert outcome.envelope.result is not None
    assert UNVERIFIED_ID not in outcome.trace.evidence_ids


def test_live_answer_citing_a_fabricated_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _live_settings()
    monkeypatch.setattr(gateway_module, "get_settings", lambda: settings)

    payload = {
        "question": "What is the position?",
        "answer": "According to a source that does not exist.",
        "citations": ["not-a-real-citation-id"],
        "caveats": [],
        "answered_from_approved_sources": True,
        "confidence": 0.9,
    }
    monkeypatch.setattr(
        gateway_module,
        "_call_provider",
        lambda **_: _ProviderResponse(
            payload=payload, model_used="claude-test", input_tokens=1, output_tokens=1
        ),
    )

    _supported_grounding(monkeypatch)
    outcome = _go(AiPurpose.KNOWLEDGE_ANSWER, context=KNOWLEDGE_QUESTION)
    assert outcome.trace.fallback_reason == FallbackReason.CITATION_CHECK_FAILED.value
    assert "not-a-real-citation-id" not in outcome.trace.evidence_ids
    assert outcome.trace.evidence_ids == [GROUNDED_CITATION]


def test_snapshot_citing_unauthorised_evidence_is_refused_not_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0002: a snapshot is a cached answer, not an authorisation exemption."""
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    principal = principal_for_role(RoleCode.CONSULAR_OFFICER)
    authorised = authorise_evidence(principal, sector_codes=spec.default_sector_codes)
    outside = "covalent-lithium-our-project"
    assert outside not in authorised.ids, "fixture assumption: this id is outside the set"

    genuine = resolve_snapshot(spec, "passport-renewal")
    assert genuine is not None
    tampered_result = spec.output_schema.model_validate(
        {**genuine.result.model_dump(), "citations": [outside]}
    )
    tampered = fallback_module.Snapshot(
        purpose=spec.purpose,
        scenario="passport-renewal",
        key="consular_triage_passport-renewal",
        path=Path("tampered"),
        result=tampered_result,
        evidence_ids=(outside,),
        approval_status=spec.approval_status,
    )
    monkeypatch.setattr(gateway_module, "resolve_snapshot", lambda *_a, **_k: tampered)

    outcome = _go(
        AiPurpose.CONSULAR_TRIAGE,
        role=RoleCode.CONSULAR_OFFICER,
        context=GatewayContext(subject_ref="passport-renewal", facts=TRIAGE_FACTS),
    )
    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    assert outcome.envelope.result is None
    assert outcome.trace.citation_check_passed is False
    assert "refused rather than shown" in (outcome.envelope.explanation or "")


# --------------------------------------------------------------------------- no snapshot


def _redirect_snapshots(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Point every snapshot lookup at ``directory`` and drop the cache."""
    monkeypatch.setattr(config_module, "SNAPSHOT_DIR", directory)
    monkeypatch.setattr(fallback_module, "SNAPSHOT_DIR", directory)
    clear_snapshot_cache()


def test_no_snapshot_at_all_returns_a_blocked_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The last-resort path: explained, non-crashing, and never half-populated."""
    _redirect_snapshots(monkeypatch, tmp_path / "empty")

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    envelope = outcome.envelope

    assert envelope.approval_status is ApprovalStatus.BLOCKED
    assert envelope.result is None
    assert envelope.evidence == []
    assert envelope.trace_id
    explanation = envelope.explanation or ""
    assert "unavailable" in explanation
    assert "__default__" in explanation
    assert "Nothing was fabricated" in explanation
    assert _stage(outcome, "schema_validation").ok is False
    assert outcome.trace.fallback is False, "no snapshot was served, so none is claimed"
    assert outcome.trace.fallback_reason is None


def test_missing_scenario_uses_the_default_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A specific miss must degrade to ``__default__`` rather than to BLOCKED."""
    source = snapshot_file(AiPurpose.MORNING_BRIEF, DEFAULT_SCENARIO)
    target_dir = tmp_path / "defaults-only"
    target_dir.mkdir()
    (target_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _redirect_snapshots(monkeypatch, target_dir)

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.envelope.result is not None
    assert outcome.trace.scenario == "ambassador"
    assert outcome.trace.snapshot_key == f"morning_brief_{DEFAULT_SCENARIO}"


def test_malformed_snapshot_is_a_miss_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A JSON typo in a demo asset degrades to the default; it never reaches the audience."""
    target_dir = tmp_path / "one-broken"
    target_dir.mkdir()
    default_source = snapshot_file(AiPurpose.MORNING_BRIEF, DEFAULT_SCENARIO)
    (target_dir / default_source.name).write_text(
        default_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target_dir / "morning_brief_ambassador.json").write_text("{ this is not json", "utf-8")
    _redirect_snapshots(monkeypatch, target_dir)

    assert load_snapshot(PURPOSES[AiPurpose.MORNING_BRIEF], "ambassador") is None

    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.envelope.result is not None
    assert outcome.trace.snapshot_key == f"morning_brief_{DEFAULT_SCENARIO}"


def test_snapshot_for_the_wrong_purpose_is_a_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target_dir = tmp_path / "mislabelled"
    target_dir.mkdir()
    wrong = snapshot_file(AiPurpose.DIASPORA_MATCH, DEFAULT_SCENARIO).read_text(encoding="utf-8")
    (target_dir / f"morning_brief_{DEFAULT_SCENARIO}.json").write_text(wrong, encoding="utf-8")
    _redirect_snapshots(monkeypatch, target_dir)

    assert load_snapshot(PURPOSES[AiPurpose.MORNING_BRIEF], DEFAULT_SCENARIO) is None


def test_snapshot_cache_is_invalidatable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = snapshot_file(AiPurpose.MORNING_BRIEF, DEFAULT_SCENARIO)
    body = source.read_text(encoding="utf-8")
    target_dir = tmp_path / "late-arrival"
    target_dir.mkdir()
    _redirect_snapshots(monkeypatch, target_dir)
    spec = PURPOSES[AiPurpose.MORNING_BRIEF]

    assert load_snapshot(spec, DEFAULT_SCENARIO) is None

    (target_dir / source.name).write_text(body, encoding="utf-8")

    assert load_snapshot(spec, DEFAULT_SCENARIO) is None, "the miss is cached, deliberately"
    clear_snapshot_cache()
    assert load_snapshot(spec, DEFAULT_SCENARIO) is not None


# --------------------------------------------------------------------------- envelope


def test_a_blocked_envelope_cannot_carry_a_result() -> None:
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    assert outcome.envelope.result is not None
    with pytest.raises(ValueError, match="BLOCKED"):
        GatewayResult(
            result=outcome.envelope.result,
            evidence=[],
            trace_id="t",
            approval_status=ApprovalStatus.BLOCKED,
        )


def test_a_successful_envelope_cannot_omit_its_result() -> None:
    with pytest.raises(ValueError, match="BLOCKED"):
        GatewayResult(
            result=None,
            evidence=[],
            trace_id="t",
            approval_status=ApprovalStatus.NOT_REQUIRED,
        )


def test_envelope_serialises_the_concrete_result_not_an_empty_object() -> None:
    """``SerializeAsAny``: without it every answer in the system would serialise as ``{}``."""
    outcome = _go(AiPurpose.MORNING_BRIEF, role=RoleCode.AMBASSADOR)
    body = outcome.envelope.model_dump(mode="json")
    assert set(body) >= {"result", "evidence", "trace_id", "approval_status"}
    assert body["result"]["headline"]
    assert body["evidence"][0]["url"].startswith("http")
    assert body["evidence"][0]["citation_id"] == body["evidence"][0]["id"]
