"""The live provider path, exercised without a key, a network call or a bill.

There is no ``ANTHROPIC_API_KEY`` on the build machine and there must never be one in CI,
so "does live generation work" cannot be answered by calling Anthropic. It is answered by
substituting the SDK: ``sys.modules["anthropic"]`` is replaced with a stub whose
``messages.parse`` returns a validated result, raises, or sleeps past the budget. That
exercises every branch of the code we own - the structured-output call, the timeout, the
error classification, the trace fields - and none of the code we do not.

What this deliberately does NOT prove: that the real API accepts our request shape. Only a
real call proves that, and it needs a key. See ``docs/W2_STATUS.md``.
"""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Iterator
from typing import Any

import pytest

from app.ai import gateway
from app.ai.schemas import GatewayContext, MorningBriefResult
from app.domain.enums import AiPurpose, Classification, RoleCode
from app.security.principal import principal_for_role

pytestmark = pytest.mark.usefixtures("live_gateway")

AMBASSADOR = principal_for_role(RoleCode.AMBASSADOR)

#: The consular_triage context policy accepts these metadata fields and refuses anything
#: else, narrative above all. Mirrors tests/test_gateway_pipeline.py.
TRIAGE_FACTS: dict[str, Any] = {
    "case_type": "PASSPORT_RENEWAL",
    "case_age_days": 16,
    "sla_state": "RUNNING",
    "status": "ASSIGNED",
}


def _valid_brief() -> MorningBriefResult:
    """A minimal brief that satisfies the schema, citing a VERIFIED registry id."""
    return MorningBriefResult.model_validate(
        {
            "headline": "Live-path brief",
            "as_at_label": "As at the test date",
            "summary": "A brief produced by the stubbed live path, for testing only.",
            "confidence": 0.7,
            "items": [
                {
                    "title": "Kwinana refinery is ramping toward nameplate",
                    "item_type": "SIGNAL",
                    "detail": "The refinery is ramping to its existing nameplate capacity.",
                    "so_what": "Midstream processing is where the bilateral opening sits.",
                    "confidence": 0.8,
                    "citations": ["covalent-lithium-first-product-kwinana-refinery"],
                }
            ],
        }
    )


class _StubUsage:
    input_tokens = 1234
    output_tokens = 567


class _StubMessage:
    def __init__(self, parsed: Any, model: str) -> None:
        self.parsed_output = parsed
        self.model = model
        self.usage = _StubUsage()


def _install_stub(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> dict[str, Any]:
    """Replace the ``anthropic`` module with a stub. Returns the recorded call kwargs."""
    recorded: dict[str, Any] = {}

    class _Messages:
        def parse(self, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            return behaviour(**kwargs)

    class _Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            recorded["client_kwargs"] = kwargs
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return recorded


@pytest.fixture
def live_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Turn the live path on for one test, with a key that is never used."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AI_GATEWAY_LIVE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run() -> Any:
    return gateway.generate_traced(
        AiPurpose.MORNING_BRIEF,
        Classification.PUBLIC,
        GatewayContext(scenario="ambassador"),
        AMBASSADOR,
    )


def test_a_successful_live_call_is_served_and_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _install_stub(monkeypatch, lambda **kw: _StubMessage(_valid_brief(), kw["model"]))
    outcome = _run()

    assert outcome.trace.live is True
    assert outcome.trace.fallback is False
    assert outcome.envelope.result is not None
    assert outcome.envelope.result.headline == "Live-path brief"
    # Structured output, not prose-then-parse.
    assert recorded["output_format"] is MorningBriefResult
    # The HTTP client is bounded by the same budget as the outer timeout.
    assert recorded["client_kwargs"]["timeout"] == pytest.approx(4.0)
    assert recorded["client_kwargs"]["max_retries"] == 0


def test_the_route_and_tier_are_recorded_on_a_live_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUILD_BIBLE section 4a: the routing decision is what the drawer shows."""
    _install_stub(monkeypatch, lambda **kw: _StubMessage(_valid_brief(), kw["model"]))
    outcome = _run()

    assert outcome.trace.model_route == "external"
    assert outcome.trace.model_tier == "strong"
    assert outcome.trace.route_badge.startswith("PUBLIC · external · ")
    assert outcome.trace.route_badge.endswith(" · strong")
    assert outcome.trace.model_used
    assert outcome.trace.input_tokens == 1234


def test_a_timeout_falls_back_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ≤4s budget is the contract; past it the demo serves the snapshot."""

    def _slow(**kwargs: Any) -> Any:
        time.sleep(6.0)
        raise AssertionError("unreachable: the budget should have expired")

    _install_stub(monkeypatch, _slow)
    started = time.monotonic()
    outcome = _run()
    elapsed = time.monotonic() - started

    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == "TIMEOUT"
    # The caller waits the budget, not the provider.
    assert elapsed < 5.5, f"the budget did not bound the wait ({elapsed:.1f}s)"
    # And the brief still renders.
    assert outcome.envelope.result is not None
    assert outcome.envelope.approval_status.value != "BLOCKED"


@pytest.mark.parametrize(
    ("exception_name", "expected_reason"),
    [
        ("RateLimitError", "RATE_LIMIT"),
        ("APITimeoutError", "TIMEOUT"),
        ("AuthenticationError", "NO_API_KEY"),
        ("APIStatusError", "API_ERROR"),
    ],
)
def test_provider_errors_fall_back_with_a_specific_reason(
    monkeypatch: pytest.MonkeyPatch, exception_name: str, expected_reason: str
) -> None:
    """Every provider failure resolves to the snapshot, with the reason named."""
    error_type = type(exception_name, (Exception,), {})

    def _raise(**kwargs: Any) -> Any:
        raise error_type("stubbed provider failure")

    _install_stub(monkeypatch, _raise)
    outcome = _run()

    assert outcome.trace.fallback is True
    assert outcome.trace.fallback_reason == expected_reason
    assert outcome.envelope.result is not None


def test_an_unparseable_response_falls_back_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 is not the same as a usable answer."""
    _install_stub(monkeypatch, lambda **kw: _StubMessage(None, kw["model"]))
    outcome = _run()

    assert outcome.trace.fallback is True
    assert outcome.envelope.result is not None


def test_consular_triage_never_reaches_the_provider_even_with_live_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strongest property in this file.

    Live is on and a key is present, and the purpose still makes no call. If this ever
    fails, consular process metadata is going to a third party.
    """
    called = {"n": 0}

    def _record(**kwargs: Any) -> Any:
        called["n"] += 1
        raise AssertionError("consular_triage must not reach a provider")

    _install_stub(monkeypatch, _record)
    outcome = gateway.generate_traced(
        AiPurpose.CONSULAR_TRIAGE,
        Classification.MISSION_INTERNAL,
        GatewayContext(facts=TRIAGE_FACTS),
        principal_for_role(RoleCode.CONSULAR_OFFICER),
    )

    assert called["n"] == 0
    assert outcome.trace.live is False
    assert outcome.trace.model_requested is None
    # The BAND (MISSION_INTERNAL) would permit an external call; the PURPOSE declines it,
    # and the badge says which of the two withheld it. That distinction is the point: a
    # reader must be able to tell "the zone forbids this" from "this purpose chooses not
    # to", because they are different assurances.
    assert "purpose withholds external" in outcome.trace.route_badge
    assert "metadata-only" in outcome.trace.route_badge
