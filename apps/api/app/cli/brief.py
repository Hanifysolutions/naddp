"""``python -m app.cli.brief`` -- generate, inspect and move a morning brief.

THE COMPOSITION ROOT for brief generation. ADR-0001 keeps ``app/services/`` from importing
``app.ai``, so ``app.services.briefs`` declares ports and this module binds
``app.ai.gateway.generate``, ``app.ai.schemas.GatewayContext`` and
``app.ai.evidence.evidence_refs_for`` to them.

Usage::

    python -m app.cli.brief generate --role AMBASSADOR
    python -m app.cli.brief show --role AMBASSADOR
    python -m app.cli.brief advance --role AMBASSADOR --event submit --as DEPUTY
"""

from __future__ import annotations

import argparse
from datetime import date

from app.ai.evidence import evidence_refs_for
from app.ai.gateway import generate as gateway_generate
from app.ai.schemas import GatewayContext
from app.core.db import session_scope
from app.core.logging import configure_logging
from app.domain.enums import RoleCode
from app.security.principal import principal_for_role
from app.services.briefs import brief_for, generate_brief, transition_brief


def _context() -> GatewayContext:
    """An EMPTY context. Deliberately.

    MORNING_BRIEF's context policy refuses a free-text question, and rightly: a brief
    summarises a known corpus rather than answering whatever a caller types, and accepting
    caller prose here would be an injection surface pointed straight at the one artefact
    the Ambassador reads aloud. Stage 3 therefore builds its retrieval query from the
    purpose's own declared sector codes, which no caller can influence.

    An earlier draft of this CLI passed a hand-written question. The Gateway refused it,
    which is the guard working.
    """
    return GatewayContext()


def _generate(role: RoleCode, brief_date: date) -> int:
    principal = principal_for_role(role)
    with session_scope() as session:
        generated = generate_brief(
            session,
            principal,
            brief_date=brief_date,
            generate=gateway_generate,
            context_factory=_context,
            resolve_evidence=evidence_refs_for,
        )
        brief = generated.brief
        print(f"\n{'=' * 78}")
        print(f"MORNING BRIEF - {role.value} - {brief_date}")
        print(f"{'=' * 78}")
        print(f"  {brief.title}")
        print(f"\n  {brief.summary}\n")
        print(f"  status      : {brief.status.value}")
        print(f"  trace       : {generated.trace_id}")
        print(f"  route badge : {generated.route_badge or '(not recorded)'}")
        print(f"  fallback    : {generated.fallback}")
        print(f"  items       : {generated.item_count}")
        for item in sorted(brief.items, key=lambda i: i.position):
            print(f"\n  [{item.position + 1}] {item.headline}")
            print(f"      type={item.item_type.value}  confidence={item.confidence}")
            print(f"      so what: {item.so_what[:100]}")
            for ref in item.evidence:
                print(f"      - {ref['citation_id']}")
                print(f"        {ref['url'] or '(no public url)'}")
    return 0


def _show(role: RoleCode, brief_date: date) -> int:
    with session_scope() as session:
        brief = brief_for(session, brief_date=brief_date, role=role)
        if brief is None:
            print(f"no brief for {role.value} on {brief_date}")
            return 1
        print(f"{role.value} {brief_date}: {brief.status.value} - {brief.title}")
        print(f"  items: {len(brief.items)}  trace: {brief.trace_id}")
    return 0


def _advance(role: RoleCode, brief_date: date, event: str, actor: RoleCode) -> int:
    with session_scope() as session:
        brief = brief_for(session, brief_date=brief_date, role=role)
        if brief is None:
            print(f"no brief for {role.value} on {brief_date}")
            return 1
        before = brief.status.value
        updated = transition_brief(session, principal_for_role(actor), brief.id, event)
        print(f"{actor.value} fired {event!r}: {before} -> {updated.status.value}")
    return 0


def main() -> int:
    configure_logging(level="WARNING")
    parser = argparse.ArgumentParser(description="NADDP morning brief.")
    parser.add_argument("command", choices=["generate", "show", "advance"])
    parser.add_argument("--role", default="AMBASSADOR", choices=[r.value for r in RoleCode])
    parser.add_argument("--event", default="submit")
    parser.add_argument("--as", dest="actor", default=None, choices=[r.value for r in RoleCode])
    parser.add_argument("--date", default=None, help="ISO date; defaults to today.")
    args = parser.parse_args()

    role = RoleCode(args.role)
    brief_date = date.fromisoformat(args.date) if args.date else date.today()

    if args.command == "generate":
        return _generate(role, brief_date)
    if args.command == "show":
        return _show(role, brief_date)
    return _advance(role, brief_date, args.event, RoleCode(args.actor or args.role))


if __name__ == "__main__":
    raise SystemExit(main())
