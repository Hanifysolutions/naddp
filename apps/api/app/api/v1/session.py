"""``/v1/session`` -- the demo role picker, and the payload the web navigation is built on.

Three routes:

* ``POST /v1/session/assume-role`` -- become a role. Sets the signed cookie and writes a
  ``session.role_assumed`` audit row.
* ``GET  /v1/session/me`` -- who am I, what may I do, what may I read. The web shell calls
  this once and renders its navigation rail from ``permissions`` (ADR-0003 rule 5); there
  is no role branching anywhere in ``apps/web``.
* ``POST /v1/session/end`` -- clear the cookie.

**These are the only routes in the API without a ``require(...)`` dependency, and that is
deliberate.** ``assume-role`` is how a caller *obtains* a principal, so it cannot demand
one; ``me`` and ``end`` are gated by ``get_principal``, which is the deny-by-default check
in its purest form -- no valid session, no answer. The route-coverage test in ADR-0003's
enforcement table must allowlist exactly these three alongside ``/health`` and the OpenAPI
schema.

**Cookie flags.** ``httponly`` (script cannot read it), ``samesite=lax`` and ``secure``
outside a local environment. One caveat worth stating rather than discovering during the
deploy rehearsal: ``lax`` works for the local demo because ``localhost:3000`` and
``localhost:8000`` are the same site -- SameSite ignores the port -- but a split deployment
across two registrable domains (Vercel and Railway) is cross-site, and the cookie will not
be sent. That deployment needs ``SameSite=None; Secure`` plus explicit CORS credentials, or
the two surfaces behind one domain. Escalated in this track's handoff and owed an
``docs/OPEN_QUESTIONS.md`` entry before the Week 4 deploy; the flag is deliberately left as
``lax`` here rather than pre-emptively widened, because ``None`` on a same-site demo is a
weaker cookie for no benefit.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.config import get_settings
from app.core.db import get_session
from app.domain.enums import Classification, RoleCode
from app.security.deps import CurrentPrincipal, readable_classifications
from app.security.permissions import SENSITIVE_PERMISSIONS
from app.security.principal import Principal, demo_persona
from app.security.session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    issue_session,
)
from app.services.session import assume_role

router = APIRouter(prefix="/session", tags=["session"])

#: Trimmed to what a log line and a 256-character column can hold without truncation
#: surprises. The audit row records the client, not a forensic fingerprint of it.
_USER_AGENT_MAX_LENGTH: Final[int] = 256


class AssumeRoleRequest(BaseModel):
    """Which of the six demo roles to become."""

    role: RoleCode = Field(description="The role to assume. One of the six demo personas.")


class SessionResponse(BaseModel):
    """The current principal, and everything the client needs to render itself.

    Everything here is derived server-side from the role on every request. The cookie
    carries only the role, so a change to the permission matrix takes effect on the next
    call rather than when a cookie happens to expire.
    """

    role: RoleCode = Field(description="The role this session acts as.")
    user_id: str = Field(description="The persona's stable id, and the actor on audit rows.")
    email: str = Field(description="Synthetic demo address on a non-routable domain.")
    full_name: str = Field(description="Display name, as it appears on audit timelines.")
    title: str = Field(description="Job title shown in the role picker.")
    mission: str = Field(description="Posting, e.g. 'Nigerian High Commission, Canberra'.")
    clearance_rank: int = Field(
        description="ADR-0006 clearance rank. Compared against a zone's minimum to read."
    )
    compartments: list[str] = Field(
        description=(
            "Need-to-know compartments held. Only the consular compartment exists; "
            "holding it is what opens a case file, not seniority."
        )
    )
    permissions: list[str] = Field(
        description=(
            "Every permission this role holds, in verb:object form. The navigation rail "
            "is the intersection of this list and the client's catalogue; the server "
            "re-checks on every request and is the only authority."
        )
    )
    sensitive_permissions: list[str] = Field(
        description=(
            "The subset gating a non-autonomous control or a bulk export. Advisory: the "
            "UI badges them. Never an authorisation input on its own."
        )
    )
    readable_classifications: list[Classification] = Field(
        description="The zones this principal is cleared to read (ADR-0006)."
    )
    is_demo_identity: bool = Field(
        description=(
            "Always true in this build. Identity is faked by the role picker; "
            "authorisation and audit are real (ADR-0003)."
        )
    )
    expires_in_seconds: int = Field(
        description="Lifetime of the signed session cookie from the moment it was issued."
    )


def _session_response(principal: Principal) -> SessionResponse:
    """Project a principal into the wire shape, adding its persona's display fields."""
    persona = demo_persona(principal.role)
    held_sensitive = principal.permissions & SENSITIVE_PERMISSIONS
    return SessionResponse(
        role=principal.role,
        user_id=str(principal.user_id),
        email=principal.email,
        full_name=principal.full_name,
        title=persona.title,
        mission=persona.mission,
        clearance_rank=principal.clearance_rank,
        compartments=sorted(principal.compartments),
        permissions=sorted(code.value for code in principal.permissions),
        sensitive_permissions=sorted(code.value for code in held_sensitive),
        readable_classifications=readable_classifications(principal),
        is_demo_identity=principal.is_demo_identity,
        expires_in_seconds=SESSION_MAX_AGE_SECONDS,
    )


def _client_ip(request: Request) -> str | None:
    """Best-effort client address for the audit row.

    Deliberately reads ``request.client`` and **not** ``X-Forwarded-For``: a forwarded
    header is caller-controlled, and a caller-controlled value written into an evidentiary
    table is worse than a null. Behind a real proxy this is configured at the ASGI layer
    (uvicorn ``--proxy-headers`` with trusted hosts), which rewrites ``request.client``
    from a header the proxy is trusted to have set.
    """
    return request.client.host if request.client is not None else None


def _user_agent(request: Request) -> str | None:
    """Client user agent, truncated to the column width."""
    raw = request.headers.get("user-agent")
    return raw[:_USER_AGENT_MAX_LENGTH] if raw else None


def _set_session_cookie(response: Response, role: RoleCode) -> None:
    """Attach the signed demo session cookie. See the module docstring on ``samesite``."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session(role),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=not get_settings().is_local,
        path="/",
    )


@router.post(
    "/assume-role",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Assume a demo role",
    response_description="The resulting session, with its permission and clearance sets.",
)
def assume_role_endpoint(
    payload: AssumeRoleRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
) -> SessionResponse:
    """Become one of the six demo roles.

    NOT AUTHENTICATION. No credential is presented and none is checked (ADR-0003). The
    cookie this sets is signed so it cannot be edited client-side, which makes the trust
    boundary honestly shaped, but anyone who can reach this endpoint can become any role.
    The deployed demo therefore carries only synthetic data and should sit behind a
    deployment-level access gate.

    Writes a ``session.role_assumed`` audit row before returning, so every later row in the
    log is attributable to a recorded assumption.
    """
    principal = assume_role(
        db,
        role=payload.role,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    _set_session_cookie(response, principal.role)
    return _session_response(principal)


@router.get(
    "/me",
    response_model=SessionResponse,
    summary="The current session",
    response_description="The caller's principal, permissions and readable classifications.",
)
def read_me(principal: CurrentPrincipal) -> SessionResponse:
    """Return the current principal.

    The web shell's single source for navigation: it renders the intersection of
    ``permissions`` and its own route catalogue, so adding a screen means adding a
    permission on the server and a row in the catalogue, and nothing else.

    Returns 403 when there is no valid session -- deny-by-default, with no anonymous tier.
    """
    return _session_response(principal)


@router.post(
    "/end",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the current session",
    response_description="The session cookie has been cleared.",
)
def end_session(response: Response, principal: CurrentPrincipal) -> None:
    """Clear the session cookie.

    Requires a valid session, so that ending one is an act by somebody rather than an
    anonymous request that quietly succeeds. The cookie is deleted with the same flags it
    was set with -- a browser matches on name, path and domain, and a mismatch leaves the
    original cookie in place while returning 204.

    No audit row: ADR-0004's closed vocabulary names ``session.role_assumed`` and no
    counterpart, and inventing an action string here would put this module's spelling ahead
    of ``app/audit/actions.py``, which the workflow track owns. Whether a ``session.ended``
    action belongs in that vocabulary is escalated in this track's handoff -- it is a small
    decision, but it is the vocabulary module's to make, not this handler's.
    """
    _ = principal  # the dependency is the gate; the value is not otherwise needed
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=not get_settings().is_local,
        path="/",
    )
