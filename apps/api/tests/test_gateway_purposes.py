"""The purpose allowlist, the classification ceilings, routing and the context policies.

Pure-data tests: no snapshots, no database, no pipeline. They pin the contract that
``app/ai/gateway.py`` then enforces.
"""

from __future__ import annotations

import pytest

from app.ai.purposes import (
    DEFAULT_SCENARIO,
    PURPOSES,
    PurposeNotAllowedError,
    check_context,
    resolve_purpose,
    route_for,
    scenario_for,
)
from app.ai.schemas import GatewayContext, GroundedResult
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode

REGISTERED = [
    AiPurpose.MORNING_BRIEF,
    AiPurpose.OPPORTUNITY_SCORE,
    AiPurpose.MEETING_PREP,
    AiPurpose.MEETING_FOLLOWUP,
    AiPurpose.CONSULAR_TRIAGE,
    AiPurpose.KNOWLEDGE_ANSWER,
    AiPurpose.DIASPORA_MATCH,
]


# --------------------------------------------------------------------------- registry


def test_exactly_seven_purposes_are_registered() -> None:
    assert set(PURPOSES) == set(REGISTERED)
    assert len(PURPOSES) == 7


def test_registry_is_total_over_the_enum() -> None:
    """A new AiPurpose member with no spec must not be silently unregistered."""
    assert set(PURPOSES) == set(AiPurpose)


@pytest.mark.parametrize("purpose", REGISTERED)
def test_every_purpose_has_a_schema_and_a_route(purpose: AiPurpose) -> None:
    spec = PURPOSES[purpose]
    assert issubclass(spec.output_schema, GroundedResult)
    assert spec.default_model_route
    assert spec.summary, "a purpose with no summary has no system prompt and no drawer text"


@pytest.mark.parametrize(
    "unregistered",
    ["MORNING_BRIEF", "general_chat", None, 42, object()],
)
def test_unregistered_purpose_is_refused(unregistered: object) -> None:
    """Stage 1 is a closed allowlist, including against a string that spells a real member."""
    with pytest.raises(PurposeNotAllowedError):
        resolve_purpose(unregistered)


def test_resolve_purpose_accepts_every_member() -> None:
    for purpose in AiPurpose:
        assert resolve_purpose(purpose).purpose is purpose


# --------------------------------------------------------------------------- classification


def test_no_purpose_may_process_consular_sensitive() -> None:
    """OPEN_QUESTIONS Q-06, conservative option (c), expressed as data.

    This is the single most load-bearing assertion in the AI track: no registered purpose
    may process CONSULAR_SENSITIVE content, so case narrative can never reach a model.
    """
    for spec in PURPOSES.values():
        assert not spec.permits(Classification.CONSULAR_SENSITIVE), spec.purpose.value


def test_consular_triage_is_capped_at_mission_internal() -> None:
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    assert spec.max_classification is Classification.MISSION_INTERNAL
    assert spec.permits(Classification.PUBLIC)
    assert spec.permits(Classification.MISSION_INTERNAL)
    assert not spec.permits(Classification.CONFIDENTIAL)


def test_a_confidential_purpose_does_not_thereby_admit_consular() -> None:
    """The rank trap: CONSULAR_SENSITIVE ranks BELOW CONFIDENTIAL but is not implied by it."""
    spec = PURPOSES[AiPurpose.OPPORTUNITY_SCORE]
    assert spec.max_classification is Classification.CONFIDENTIAL
    assert spec.permits(Classification.CONFIDENTIAL)
    assert not spec.permits(Classification.CONSULAR_SENSITIVE)


def test_every_purpose_permits_public() -> None:
    for spec in PURPOSES.values():
        assert spec.permits(Classification.PUBLIC)


# --------------------------------------------------------------------------- approval


def test_consequential_purposes_return_pending_approval() -> None:
    consequential = {purpose for purpose, spec in PURPOSES.items() if spec.consequential}
    assert consequential == {AiPurpose.MEETING_FOLLOWUP, AiPurpose.CONSULAR_TRIAGE}
    for purpose in consequential:
        assert PURPOSES[purpose].approval_status is ApprovalStatus.PENDING_APPROVAL


def test_non_consequential_purposes_require_no_approval() -> None:
    for spec in PURPOSES.values():
        if not spec.consequential:
            assert spec.approval_status is ApprovalStatus.NOT_REQUIRED


# --------------------------------------------------------------------------- context policy


def test_consular_triage_rejects_a_narrative_field() -> None:
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    context = GatewayContext(
        facts={"case_type": "PASSPORT_RENEWAL", "case_narrative": "The applicant said ..."}
    )
    refusal = check_context(spec, context)
    assert refusal is not None
    assert refusal.reason == "field_not_allowlisted"
    assert refusal.offending_fields == ("case_narrative",)


@pytest.mark.parametrize(
    "field_name",
    ["narrative", "notes", "description", "citizen_statement", "correspondence", "details"],
)
def test_consular_triage_rejects_every_spelling_of_narrative(field_name: str) -> None:
    """The allowlist holds whatever the field is called -- a blocklist would not."""
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    refusal = check_context(spec, GatewayContext(facts={field_name: "some free text"}))
    assert refusal is not None
    assert refusal.reason == "field_not_allowlisted"


def test_consular_triage_rejects_a_narrative_smuggled_into_an_allowed_field() -> None:
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    smuggled = "The applicant is a student whose passport expired while " * 3
    refusal = check_context(spec, GatewayContext(facts={"status": smuggled}))
    assert refusal is not None
    assert refusal.reason == "value_too_long"
    assert refusal.offending_fields == ("status",)


def test_consular_triage_rejects_a_free_text_question() -> None:
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    refusal = check_context(spec, GatewayContext(question="Why is this case late?"))
    assert refusal is not None
    assert refusal.reason == "question_not_accepted"


def test_consular_triage_accepts_the_four_permitted_metadata_fields() -> None:
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    context = GatewayContext(
        subject_ref="NADDP-3K7QW9XZ-2M4T",
        facts={
            "case_type": "PASSPORT_RENEWAL",
            "case_age_days": 16,
            "sla_state": "RUNNING",
            "status": "ASSIGNED",
        },
    )
    assert check_context(spec, context) is None


def test_only_query_shaped_purposes_take_a_free_text_question() -> None:
    """A purpose that summarises a known object accepts no user-supplied prompt.

    Two purposes are genuinely query-shaped -- a knowledge question and a diaspora
    capability search -- and every other purpose refuses free text, which is what keeps
    "summarise this meeting" from quietly becoming a general chat box.
    """
    accepting = {
        purpose for purpose, spec in PURPOSES.items() if spec.context_policy.allows_question
    }
    assert accepting == {AiPurpose.KNOWLEDGE_ANSWER, AiPurpose.DIASPORA_MATCH}


def test_gateway_context_forbids_unknown_arguments() -> None:
    """``extra="forbid"``: a caller inventing a narrative argument gets an error, not silence."""
    with pytest.raises(ValueError, match="case_narrative"):
        GatewayContext(case_narrative="the story of the case")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- scenario keys


def test_scenario_is_a_pure_function_of_purpose_role_and_subject() -> None:
    spec = PURPOSES[AiPurpose.OPPORTUNITY_SCORE]
    context = GatewayContext(subject_ref="AU-Lithium NG Skills Corridor")
    first = scenario_for(spec, RoleCode.TRADE_OFFICER, context)
    second = scenario_for(spec, RoleCode.TRADE_OFFICER, context)
    assert first == second == "au-lithium-ng-skills-corridor"


def test_scenario_includes_the_role_only_where_the_purpose_says_so() -> None:
    brief = PURPOSES[AiPurpose.MORNING_BRIEF]
    score = PURPOSES[AiPurpose.OPPORTUNITY_SCORE]
    empty = GatewayContext()
    assert scenario_for(brief, RoleCode.AMBASSADOR, empty) == "ambassador"
    assert scenario_for(score, RoleCode.AMBASSADOR, empty) == DEFAULT_SCENARIO


def test_scenario_falls_back_to_the_default_key() -> None:
    spec = PURPOSES[AiPurpose.KNOWLEDGE_ANSWER]
    assert scenario_for(spec, RoleCode.ADMIN, GatewayContext()) == DEFAULT_SCENARIO


def test_scenario_is_filename_safe_and_bounded() -> None:
    spec = PURPOSES[AiPurpose.KNOWLEDGE_ANSWER]
    hostile = GatewayContext(scenario="../../etc/passwd; DROP TABLE ai_traces")
    key = scenario_for(spec, RoleCode.ADMIN, hostile)
    assert key.replace("-", "").replace("_", "").isalnum()
    assert "/" not in key and ".." not in key
    assert len(key) <= 96


# --------------------------------------------------------------------------- routing


def test_route_records_a_model_and_a_readable_reason() -> None:
    """Route names are the BAND's, not the purpose's (BUILD_BIBLE section 4a).

    Superseded the Q-12 placeholder, which named the purpose's own lane. 4a routes by data
    class, so MISSION-INTERNAL is 'external-noret' whatever the purpose.
    """
    spec = PURPOSES[AiPurpose.MORNING_BRIEF]
    route = route_for(spec, Classification.MISSION_INTERNAL)
    assert route.route == "external-noret"
    assert route.model_requested
    assert route.tier == "strong"
    assert route.live_eligible is True
    assert "MISSION-INTERNAL" in route.reason


def test_public_briefs_take_the_strong_tier_and_scores_the_fast_one() -> None:
    """Section 4a: capability tier is the SECONDARY key -- fast to score, strong to brief."""
    brief = route_for(PURPOSES[AiPurpose.MORNING_BRIEF], Classification.PUBLIC)
    score = route_for(PURPOSES[AiPurpose.OPPORTUNITY_SCORE], Classification.PUBLIC)
    assert (brief.route, brief.tier) == ("external", "strong")
    assert (score.route, score.tier) == ("external", "fast")
    assert brief.model_requested != score.model_requested


def test_the_badge_is_the_section_4a_string() -> None:
    """The drawer renders this verbatim; 4a requires it legible to a non-technical reader."""
    route = route_for(PURPOSES[AiPurpose.MORNING_BRIEF], Classification.PUBLIC)
    assert route.badge.startswith("PUBLIC · external · ")
    assert route.badge.endswith(" · strong")
    assert route.model_requested is not None
    assert route.model_requested in route.badge


def test_the_tier_never_widens_the_band() -> None:
    """A 'strong' purpose in a no-external band still gets no external call."""
    for zone in (Classification.CONSULAR_SENSITIVE, Classification.CONFIDENTIAL):
        route = route_for(PURPOSES[AiPurpose.MORNING_BRIEF], zone)
        assert route.model_requested is None
        assert route.live_eligible is False


def test_only_public_and_mission_internal_are_live_eligible() -> None:
    """This week the Gateway goes live for two bands only; the rest serve deterministically."""
    spec = PURPOSES[AiPurpose.MORNING_BRIEF]
    live = {zone for zone in Classification if route_for(spec, zone).live_eligible}
    assert live == {Classification.PUBLIC, Classification.MISSION_INTERNAL}


def test_confidential_context_takes_the_restricted_lane() -> None:
    spec = PURPOSES[AiPurpose.OPPORTUNITY_SCORE]
    route = route_for(spec, Classification.CONFIDENTIAL)
    assert route.route.startswith("restricted-")
    assert "CONFIDENTIAL" in route.reason


def test_consular_sensitive_never_requests_an_external_model() -> None:
    """Unreachable in practice (stage 2 refuses first) and recorded anyway, for the drawer."""
    for spec in PURPOSES.values():
        route = route_for(spec, Classification.CONSULAR_SENSITIVE)
        assert route.route == "no-external-model"
        assert route.model_requested is None
        assert route.live_eligible is False
        assert "no external route" in route.badge


def test_consular_triage_never_goes_live_in_any_band() -> None:
    """Section 4a routes by data class, and consular_triage caps at MISSION_INTERNAL
    because narrative never enters it -- so the table alone would send its de-identified
    metadata to an external provider. That is a decision about consular data nobody has
    taken, so the purpose declines its external lane in every band."""
    spec = PURPOSES[AiPurpose.CONSULAR_TRIAGE]
    assert spec.live_eligible is False
    for zone in Classification:
        route = route_for(spec, zone)
        assert route.model_requested is None, zone.value
        assert route.live_eligible is False, zone.value
