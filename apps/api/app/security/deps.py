"""FastAPI dependencies enforcing the two authorisation gates.

Gate 1 -- **permission**: may this role perform this verb on this object type?
:func:`require` answers it from ``app.security.matrix``.

Gate 2 -- **classification**: may this principal read content in this zone?
:func:`require_classification` and :func:`assert_may_read` answer it from ADR-0006.

Both must pass and neither implies the other (ADR-0003 rule 3). ``read:consular_case``
does not by itself grant sight of a ``CONSULAR_SENSITIVE`` row; the ``consular``
compartment does.

**Deny by default, with no anonymous tier.** :func:`get_principal` raises when there is no
valid session. There is no fallback role, no "public" principal and no read-only guest.
An endpoint that wants to be reachable without a session must say so explicitly by not
depending on this module, and ``/health`` and the OpenAPI schema are the only such routes.

**Filter in SQL, before the query runs.** :func:`readable_classifications` exists so that a
list or search endpoint builds ``WHERE classification IN (...)`` rather than loading rows
and dropping them afterwards. This is ``CLAUDE.md`` rule 5 and it is not stylistic:
post-filtering leaks row counts, pagination totals, facet counts and "3 more results"
affordances. A row the caller may not see must never be loaded, so it can never be counted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Protocol

from fastapi import Depends
from starlette.requests import Request

from app.core.errors import ClassificationDeniedError, PermissionDeniedError
from app.domain.enums import Classification
from app.security.permissions import Permission
from app.security.principal import (
    Principal,
    principal_for_role,
    readable_classifications_for,
)
from app.security.session import SESSION_COOKIE_NAME, read_session

__all__ = [
    "CurrentPrincipal",
    "SupportsClassification",
    "assert_may_read",
    "get_principal",
    "readable_classifications",
    "require",
    "require_classification",
]


class SupportsClassification(Protocol):
    """Anything carrying a data classification -- every ``ClassifiedMixin`` model.

    A read-only property rather than a mutable attribute, so an ORM model whose
    ``classification`` is a mapped descriptor satisfies it structurally without the
    protocol also demanding that the attribute be assignable.
    """

    @property
    def classification(self) -> Classification: ...


def get_principal(request: Request) -> Principal:
    """Resolve the request's signed demo session into a :class:`Principal`.

    **This is the faked seam and the only one** (ADR-0003). It reads a cookie; a pilot
    reads an OIDC token. Everything downstream is unchanged either way.

    Raises:
        PermissionDeniedError: when the cookie is absent, forged, expired, or names a role
            that no longer exists. All four are the same answer -- there is no session --
            and the caller is told nothing that distinguishes them, because "your cookie
            expired" and "your cookie is forged" are different invitations.
    """
    role = read_session(request.cookies.get(SESSION_COOKIE_NAME))
    if role is None:
        raise PermissionDeniedError(
            "No active session. Choose a role at POST /v1/session/assume-role.",
            extra={"reason": "no_session"},
        )
    return principal_for_role(role)


#: The current principal, for handlers that need identity but no specific permission.
#:
#: Depending on this alone is still a real gate -- it refuses an unauthenticated caller --
#: but it is not a *permission* check. Any route that performs or reveals something should
#: depend on :func:`require` instead, and the route-coverage test in ADR-0003's enforcement
#: table treats a bare ``CurrentPrincipal`` on a business endpoint as a finding.
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require(*permissions: Permission) -> Callable[[Principal], Principal]:
    """Return a dependency that admits only a principal holding **all** ``permissions``.

    ``ALL``, not any: a route that needs two capabilities needs both, and an "any of"
    default would silently widen every multi-permission route. Where "any of" is genuinely
    meant, list one permission per route.

    The denial carries the required and missing codes on the exception's ``extra``, which
    flows into the RFC 9457 problem document *and* is what the audit middleware records as
    the ``detail`` of the ``policy_result = DENY`` row. A denial that cannot say what was
    attempted is not evidence of anything (ADR-0003 rule 6), and this is the only place
    that information exists.

    Disclosing the missing permission code to the caller leaks nothing: the web client
    already receives its own permission set from ``GET /v1/session/me`` in order to render
    navigation, so it can compute the same answer. What is deliberately *not* disclosed is
    whether the object exists -- that is why the message never names one.

    Example::

        @router.post("/{case_id}/resolve", dependencies=[Depends(require(
            Permission.RESOLVE_CONSULAR_CASE,
        ))])
    """
    required = frozenset(permissions)
    if not required:
        # A dependency that requires nothing is almost certainly a mistake -- and a
        # dangerous one, because it reads like a check. Fail at import, not at request time.
        msg = "require() needs at least one permission; an empty require() authorises nothing."
        raise ValueError(msg)

    def dependency(principal: CurrentPrincipal) -> Principal:
        missing = principal.missing(required)
        if missing:
            raise PermissionDeniedError(
                extra={
                    "reason": "missing_permission",
                    "required_permissions": sorted(code.value for code in required),
                    "missing_permissions": sorted(code.value for code in missing),
                    "actor_role": principal.role.value,
                },
            )
        return principal

    return dependency


def require_classification(classification: Classification) -> Callable[[Principal], Principal]:
    """Return a dependency admitting only a principal cleared for ``classification``.

    For a route whose zone is known from the route itself -- a consular endpoint that only
    ever touches ``CONSULAR_SENSITIVE`` content, say. Where the zone is a property of the
    row that was loaded, use :func:`assert_may_read` after loading instead, and put the
    zone predicate in the query for anything that returns more than one row.
    """

    def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.may_read(classification):
            raise ClassificationDeniedError(
                extra={
                    "reason": "insufficient_clearance",
                    "classification": classification.value,
                    "actor_role": principal.role.value,
                },
            )
        return principal

    return dependency


def assert_may_read(principal: Principal, obj: SupportsClassification) -> None:
    """Raise unless ``principal`` is cleared for ``obj``'s zone.

    The **backstop**, not the control (ADR-0006). The control is the classification
    predicate inside the query. If this ever fires on a list endpoint, a query is missing
    its predicate and *that* is the bug -- the row should never have been loaded. On a
    single-object fetch by id it is the primary check, and there it is doing real work.

    Raises:
        ClassificationDeniedError: rendered as 403 with the zone named. The zone is safe to
            disclose: a caller who fetched by id already knows the object exists, and
            naming the zone is what makes the refusal legible in the demo rather than
            mysterious.
    """
    zone = obj.classification
    if not principal.may_read(zone):
        raise ClassificationDeniedError(
            extra={
                "reason": "insufficient_clearance",
                "classification": zone.value,
                "actor_role": principal.role.value,
            },
        )


def readable_classifications(principal: Principal) -> list[Classification]:
    """Return the zones ``principal`` may read, for the ``WHERE`` clause.

    Use it *before* the query::

        stmt = select(Opportunity).where(
            Opportunity.classification.in_(readable_classifications(principal))
        )

    Never after it. Post-filtering in Python returns the right rows and the wrong counts,
    and the counts are what leak: a total of 41 with 38 rows rendered tells the reader
    exactly how many records they are not cleared for, and a facet breakdown tells them
    where those records are.

    The list is never empty -- every role clears ``PUBLIC`` -- so the generated ``IN``
    clause is always valid SQL.
    """
    return readable_classifications_for(principal)
