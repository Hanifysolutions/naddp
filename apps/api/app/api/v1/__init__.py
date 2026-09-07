"""Version 1 of the NADDP HTTP API, mounted by ``app.main`` at ``/v1``.

Every router below already carries its own ``prefix`` and ``tags``, so this module passes
no prefix argument: a router that declared ``prefix="/audit"`` and was then mounted with a
second prefix here would answer on a path neither file states, and the two would drift the
first time somebody edited one of them. The rule for a new bounded context is therefore:
declare the prefix and tags on the router, add two lines here, and nothing else.

Mounted paths, which the web client and the Week 1 VERIFY block depend on exactly::

    GET  /v1/meta
    POST /v1/session/assume-role
    GET  /v1/session/me
    POST /v1/session/end
    GET  /v1/command/today
    GET  /v1/opportunities
    GET  /v1/opportunities/board
    GET  /v1/opportunities/{opportunity_id}
    POST /v1/opportunities/{opportunity_id}/transition
    GET  /v1/stakeholders/organisations
    GET  /v1/stakeholders/organisations/{organisation_id}
    GET  /v1/stakeholders/people/{stakeholder_id}
    POST /v1/ai/morning-brief
    POST /v1/ai/opportunities/{opportunity_id}/score
    POST /v1/ai/meetings/{meeting_id}/prep
    POST /v1/ai/meetings/{meeting_id}/followup
    POST /v1/ai/consular/cases/{case_id}/triage
    POST /v1/ai/knowledge/answer
    POST /v1/ai/diaspora/match
    GET  /v1/ai/traces/{trace_id}
    GET  /v1/audit/events
    GET  /v1/audit/chain

Include order is presentation only -- FastAPI matches on the path, not on registration
order, and no two routers share a prefix -- so the sequence below follows the shape of the
product: metadata, then who you are, then the day's picture, then the bounded contexts,
then the governance surface that records all of it.

The remaining contexts (``intelligence``, ``meetings``, ``consular``, ``diaspora``,
``knowledge``) add themselves the same way.
"""

from fastapi import APIRouter

from app.api.v1.ai import router as ai_router
from app.api.v1.audit import router as audit_router
from app.api.v1.command import router as command_router
from app.api.v1.meta import router as meta_router
from app.api.v1.opportunities import router as opportunities_router
from app.api.v1.session import router as session_router
from app.api.v1.stakeholders import router as stakeholders_router

router = APIRouter()
router.include_router(meta_router)
router.include_router(session_router)
router.include_router(command_router)
router.include_router(opportunities_router)
router.include_router(stakeholders_router)
router.include_router(ai_router)
router.include_router(audit_router)

__all__ = ["router"]
