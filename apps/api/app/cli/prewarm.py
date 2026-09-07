"""``python -m app.cli.prewarm`` -- warm the demo's caches before anyone is watching.

WHY THIS EXISTS. The Gateway's heavy purposes now get a 25s budget (architect ruling,
W2.2 review). 25s is the right budget for a real generation and the wrong thing to do in
front of an Ambassador: nobody watches a screen for 25 seconds. So the hero artefacts are
generated *before* the demo, while a slow network costs nothing, and the on-stage call
reads what is already there.

WHAT IT ACTUALLY GUARANTEES. This does not make the demo fast by making the model fast --
it makes the demo fast by having already asked. After a prewarm the morning brief exists
as a persisted DRAFT with its items and citations, so the on-stage path is a database read.
If prewarm never ran, the demo still works: the Gateway falls back to its deterministic
snapshot within the budget. Prewarm improves the demo; it is not load-bearing for it.

RUN IT AFTER ``make demo-reset``. Reset drops the schema, so anything warmed before it is
gone. ``make demo-prewarm`` is the step that follows.
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from app.ai.evidence import citation_registry, evidence_refs_for
from app.ai.gateway import generate as gateway_generate
from app.ai.schemas import GatewayContext
from app.core.db import session_scope
from app.core.logging import configure_logging
from app.domain.enums import RoleCode
from app.security.principal import principal_for_role
from app.services.briefs import BriefGenerationError, brief_for, generate_brief

#: The roles whose briefs the demo actually opens. Warming all six would cost four
#: generations nobody watches.
DEMO_ROLES: tuple[RoleCode, ...] = (
    RoleCode.AMBASSADOR,
    RoleCode.TRADE_OFFICER,
    RoleCode.CONSULAR_OFFICER,
)


def _progress(*, chars: int, elapsed: float) -> None:
    """Token-level feedback, so a 25s generation is visibly working rather than hung."""
    print(f"\r      ...streaming {chars:>6} chars  {elapsed:>5.1f}s", end="", flush=True)


def prewarm(brief_date: date, *, roles: tuple[RoleCode, ...] = DEMO_ROLES) -> int:
    """Generate and persist the hero briefs. Returns a process exit code."""
    registry = citation_registry()
    verified = sum(1 for entry in registry.values() if entry.verified)
    print("NADDP demo prewarm")
    print(f"  citation registry : {verified} VERIFIED of {len(registry)} loaded")

    failures: list[str] = []
    for role in roles:
        principal = principal_for_role(role)
        started = time.perf_counter()
        print(f"\n  {role.value}")
        try:
            with session_scope() as session:
                generated = generate_brief(
                    session,
                    principal,
                    brief_date=brief_date,
                    generate=gateway_generate,
                    context_factory=GatewayContext,
                    resolve_evidence=evidence_refs_for,
                    on_progress=_progress,
                )
                elapsed = time.perf_counter() - started
                print(f"\r      cached: {generated.item_count} items in {elapsed:.1f}s")
                print(f"      route : {generated.route_badge or '(not recorded)'}")
                print(
                    f"      served: {'fallback snapshot' if generated.fallback else 'live model'}"
                )
        except BriefGenerationError as exc:
            # A brief a human has already touched is not an error worth failing the run
            # for; anything else is, because it means the demo has no warmed brief.
            already = "already" in str(exc)
            print(f"\r      {'skipped' if already else 'FAILED'}: {exc}")
            if not already:
                failures.append(f"{role.value}: {exc}")

    print("\n  verifying what was cached")
    with session_scope() as session:
        for role in roles:
            brief = brief_for(session, brief_date=brief_date, role=role)
            if brief is None:
                failures.append(f"{role.value}: nothing cached")
                print(f"    {role.value:<18} MISSING")
                continue
            uncited = [item.position for item in brief.items if not item.evidence]
            print(
                f"    {role.value:<18} {len(brief.items)} items, "
                f"{brief.status.value}, all cited: {not uncited}"
            )
            if uncited:
                failures.append(f"{role.value}: items {uncited} carry no evidence")

    if failures:
        print("\n  PREWARM INCOMPLETE:")
        for failure in failures:
            print(f"    - {failure}")
        return 1
    print("\n  prewarm complete - the on-stage brief is a database read.")
    return 0


def main() -> int:
    configure_logging(level="WARNING")
    parser = argparse.ArgumentParser(description="Warm the NADDP demo caches.")
    parser.add_argument("--date", default=None, help="ISO date; defaults to today.")
    args = parser.parse_args()
    return prewarm(date.fromisoformat(args.date) if args.date else date.today())


if __name__ == "__main__":
    raise SystemExit(main())
