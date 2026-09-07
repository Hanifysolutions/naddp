"""The demo session cookie: a signed assertion of which role you are.

READ THIS BEFORE USING IT ANYWHERE.

**This is a DEMO IDENTITY, not authentication, and it must never ship as such**
(ADR-0003). It asserts *which role you are*, and it is signed with
``DEMO_SESSION_SECRET`` so it cannot be edited client-side -- but nothing in this system
ever verifies a credential. There is no password, no token exchange, no identity provider.
Anyone who can reach ``POST /v1/session/assume-role`` can become any of the six roles,
which is why the deployed demo carries only synthetic data, is badged DEMO / SYNTHETIC on
every screen, and should sit behind a deployment-level access gate.

What is real, and is the whole point of faking only this half: the permission matrix, the
classification gates, the denials and the audit log. The faked seam is exactly one
function (``app.security.principal.principal_for_role``); replacing this module with an
OIDC callback changes nothing downstream.

Why sign the cookie at all, if identity is fake? Because the *shape* of the trust boundary
should be honest. A plain cookie would teach the codebase that a client-asserted role is
acceptable input, and every later feature would inherit that assumption. Signing means the
server never trusts a role it did not issue -- which is the property real authentication
also has, so the retrofit changes the issuer, not the consumers.

Design notes:

* ``URLSafeTimedSerializer`` carries the issue time inside the signature, so expiry is
  checked cryptographically rather than by trusting a client-supplied timestamp or a
  browser to honour ``Max-Age``.
* :func:`read_session` **never raises**. A bad signature, an expired token, a payload from
  an older format and an unknown role all resolve to ``None``, and ``None`` means denied.
  A security primitive that throws different exceptions for different failures invites a
  caller to handle some and forget others; there is exactly one failure here, and it is
  "no session".
* The salt is versioned. Changing the payload format means bumping it, which invalidates
  every outstanding cookie rather than reinterpreting one under new rules.
"""

from __future__ import annotations

from typing import Any, Final

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.enums import RoleCode

__all__ = [
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE_SECONDS",
    "SESSION_PAYLOAD_VERSION",
    "issue_session",
    "read_session",
]

_logger = get_logger(__name__)

#: The cookie name, fixed by ADR-0003.
SESSION_COOKIE_NAME: Final[str] = "naddp_demo_session"

#: Twelve hours -- one working day plus the overrun of a long demo, and short enough that
#: a token left on a conference laptop stops working the same day.
SESSION_MAX_AGE_SECONDS: Final[int] = 12 * 60 * 60

#: Payload format version. Bump alongside :data:`_SIGNING_SALT` on any shape change.
SESSION_PAYLOAD_VERSION: Final[int] = 1

#: Namespaces the signature so a token minted for this purpose cannot be replayed against
#: any other use of the same secret. itsdangerous mixes the salt into the derived key.
_SIGNING_SALT: Final[str] = "naddp.demo.session.v1"

_ROLE_KEY: Final[str] = "role"
_VERSION_KEY: Final[str] = "v"


def _serializer() -> URLSafeTimedSerializer:
    """Build a serializer keyed on the current ``DEMO_SESSION_SECRET``.

    Constructed per call rather than cached at import. ``get_settings()`` is itself
    memoised so this is cheap, and it means a test (or a rotated secret in a reloaded
    process) takes effect immediately instead of signing with a value captured at import
    time -- a cached serializer would silently keep honouring the old secret, which is the
    opposite of what rotating a secret is for.
    """
    return URLSafeTimedSerializer(get_settings().demo_session_secret, salt=_SIGNING_SALT)


def issue_session(role: RoleCode) -> str:
    """Return a signed, URL-safe token asserting ``role``.

    The token carries the role and the payload version, and the signature carries the
    issue time. It carries no user id, no permission list and no clearance: those are
    derived server-side from the role on every request, so a permission change takes
    effect immediately instead of waiting for a cookie to expire.
    """
    return _serializer().dumps({_VERSION_KEY: SESSION_PAYLOAD_VERSION, _ROLE_KEY: role.value})


def read_session(
    token: str | None,
    *,
    max_age: int = SESSION_MAX_AGE_SECONDS,
) -> RoleCode | None:
    """Return the role asserted by ``token``, or ``None``.

    ``None`` is returned -- never an exception -- for every failure: no token, a forged or
    truncated signature, a token signed with a different secret, a token older than
    ``max_age``, a payload of the wrong shape or version, and a role that is not one of the
    six. Deny-by-default has exactly one failure mode, so the caller has exactly one branch
    to get right.

    Args:
        token: The raw cookie value, or ``None`` when the cookie is absent.
        max_age: Maximum token age in seconds. Overridable so a test can assert expiry
            deterministically and so a shorter window can be imposed without a redeploy;
            it never lengthens what :func:`issue_session` promised, because the issue time
            is inside the signature.
    """
    if not token:
        return None

    try:
        payload: Any = _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        _logger.info("session.token_expired")
        return None
    except BadSignature:
        # Covers a forged, truncated, re-encoded or foreign-secret token. Logged at
        # warning because, unlike an expiry, it should not happen in normal use.
        _logger.warning("session.token_signature_invalid")
        return None

    if not isinstance(payload, dict) or payload.get(_VERSION_KEY) != SESSION_PAYLOAD_VERSION:
        _logger.warning("session.token_payload_unrecognised")
        return None

    try:
        return RoleCode(payload.get(_ROLE_KEY))
    except ValueError:
        # A validly signed token naming a role that no longer exists -- for instance after
        # a role is removed. Deny rather than fall back to a default role.
        _logger.warning("session.token_role_unknown", role=str(payload.get(_ROLE_KEY)))
        return None
