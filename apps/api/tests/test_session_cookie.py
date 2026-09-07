"""The signed demo session cookie, and the deny-by-default dependency that reads it.

Pure unit tests: no database. ``get_principal`` resolves a cookie into a principal without
touching persistence, which is exactly why it can be tested this way -- and why replacing
it with an OIDC callback later touches nothing downstream.

The theme of this module is that **every failure is the same failure**. A missing cookie,
a forged one, an expired one and one naming a role that no longer exists all produce
``None`` from :func:`read_session` and a 403 from the dependency. A security primitive with
several distinguishable failure modes invites a caller to handle some and forget the rest.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from starlette.requests import Request

from app.core.config import get_settings
from app.core.errors import (
    ClassificationDeniedError,
    PermissionDeniedError,
    register_exception_handlers,
)
from app.domain.enums import Classification, RoleCode
from app.security.deps import (
    assert_may_read,
    get_principal,
    readable_classifications,
    require,
    require_classification,
)
from app.security.permissions import Permission
from app.security.principal import principal_for_role
from app.security.session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    SESSION_PAYLOAD_VERSION,
    issue_session,
    read_session,
)

# ---------------------------------------------------------------------------
# 1. Round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_round_trip_for_every_role(role: RoleCode) -> None:
    assert read_session(issue_session(role)) is role


def test_the_token_is_url_safe_and_opaque() -> None:
    """It goes in a cookie, so it must survive transport without quoting."""
    token = issue_session(RoleCode.DEPUTY)
    assert token == token.strip()
    assert all(char.isalnum() or char in "-_." for char in token), token
    # The role is signed, not encrypted -- but it must not sit there in plain sight either,
    # because a value that looks editable invites editing.
    assert "DEPUTY" not in token


def test_two_tokens_for_the_same_role_are_both_valid() -> None:
    """Timestamped signatures differ between issues; both must still resolve."""
    first = issue_session(RoleCode.ADMIN)
    second = issue_session(RoleCode.ADMIN)
    assert read_session(first) is RoleCode.ADMIN
    assert read_session(second) is RoleCode.ADMIN


def test_the_cookie_name_is_the_one_the_adr_fixes() -> None:
    assert SESSION_COOKIE_NAME == "naddp_demo_session"


def test_the_lifetime_is_twelve_hours() -> None:
    assert SESSION_MAX_AGE_SECONDS == 12 * 60 * 60


# ---------------------------------------------------------------------------
# 2. Every failure resolves to None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(None, id="absent"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("not-a-token", id="garbage"),
        pytest.param("a.b.c", id="shaped-like-a-token"),
    ],
)
def test_a_missing_or_malformed_token_is_no_session(token: str | None) -> None:
    assert read_session(token) is None


def test_a_tampered_payload_is_rejected() -> None:
    """The point of signing: the server never trusts a role it did not issue."""
    token = issue_session(RoleCode.TRADE_OFFICER)
    payload, _, signature = token.rpartition(".")
    forged = f"{payload}x.{signature}"
    assert read_session(forged) is None


def test_a_tampered_signature_is_rejected() -> None:
    token = issue_session(RoleCode.TRADE_OFFICER)
    assert read_session(token[:-1]) is None
    assert read_session(f"{token}x") is None


def test_a_token_signed_with_a_different_secret_is_rejected() -> None:
    """A cookie minted elsewhere -- or by an attacker guessing the format -- is not ours."""
    foreign = URLSafeTimedSerializer("some-other-secret", salt="naddp.demo.session.v1")
    token = foreign.dumps({"v": SESSION_PAYLOAD_VERSION, "role": RoleCode.AMBASSADOR.value})
    assert read_session(token) is None


def test_a_token_signed_with_a_different_salt_is_rejected() -> None:
    """Salt namespacing stops a token minted for another purpose being replayed here."""
    wrong_salt = URLSafeTimedSerializer(get_settings().demo_session_secret, salt="other.purpose")
    token = wrong_salt.dumps({"v": SESSION_PAYLOAD_VERSION, "role": RoleCode.AMBASSADOR.value})
    assert read_session(token) is None


def test_an_expired_token_is_rejected() -> None:
    """Expiry is checked against the issue time inside the signature, not a client clock."""
    token = issue_session(RoleCode.AMBASSADOR)
    assert read_session(token) is RoleCode.AMBASSADOR
    # A negative max_age makes any age at all too old, so this is deterministic rather
    # than dependent on the test sleeping.
    assert read_session(token, max_age=-1) is None


def test_a_validly_signed_token_naming_an_unknown_role_is_rejected() -> None:
    """Deny rather than fall back to a default role."""
    serializer = URLSafeTimedSerializer(
        get_settings().demo_session_secret, salt="naddp.demo.session.v1"
    )
    token = serializer.dumps({"v": SESSION_PAYLOAD_VERSION, "role": "SUPER_ADMIN"})
    assert read_session(token) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"role": "ADMIN"}, id="no-version"),
        pytest.param({"v": 999, "role": "ADMIN"}, id="future-version"),
        pytest.param({"v": SESSION_PAYLOAD_VERSION}, id="no-role"),
        pytest.param(["ADMIN"], id="not-an-object"),
        pytest.param("ADMIN", id="bare-string"),
    ],
)
def test_a_validly_signed_token_of_the_wrong_shape_is_rejected(payload: object) -> None:
    """A format change invalidates outstanding cookies rather than being reinterpreted."""
    serializer = URLSafeTimedSerializer(
        get_settings().demo_session_secret, salt="naddp.demo.session.v1"
    )
    assert read_session(serializer.dumps(payload)) is None


def test_read_session_never_raises() -> None:
    """One failure mode means one branch for the caller to get right."""
    for candidate in (None, "", "..", "x" * 5000, "\x00\x01", issue_session(RoleCode.ADMIN)[:3]):
        read_session(candidate)


# ---------------------------------------------------------------------------
# 3. The dependency: deny by default, no anonymous tier
# ---------------------------------------------------------------------------


def _request_with_cookie(token: str | None) -> Request:
    """A minimal ASGI scope carrying (or not carrying) the session cookie."""
    headers: list[tuple[bytes, bytes]] = []
    if token is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.mark.parametrize("role", list(RoleCode))
def test_get_principal_resolves_a_valid_cookie(role: RoleCode) -> None:
    principal = get_principal(_request_with_cookie(issue_session(role)))
    assert principal.role is role
    assert principal.permissions


def test_get_principal_denies_when_there_is_no_cookie() -> None:
    with pytest.raises(PermissionDeniedError) as excinfo:
        get_principal(_request_with_cookie(None))
    assert excinfo.value.extra["reason"] == "no_session"


def test_get_principal_denies_a_forged_cookie() -> None:
    with pytest.raises(PermissionDeniedError):
        get_principal(_request_with_cookie("forged"))


def test_there_is_no_anonymous_fallback_role() -> None:
    """An unauthenticated caller gets a refusal, never a reduced principal."""
    with pytest.raises(PermissionDeniedError):
        get_principal(_request_with_cookie(None))


# ---------------------------------------------------------------------------
# 4. require(...) over a real application
# ---------------------------------------------------------------------------


def _guarded_app() -> FastAPI:
    """A tiny app with one route behind ``require(export:bulk)``."""
    from fastapi import Depends

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/export", dependencies=[Depends(require(Permission.EXPORT_BULK))])
    def export() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/pipeline", dependencies=[Depends(require(Permission.READ_OPPORTUNITY))])
    def pipeline() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_require_admits_a_holder_and_refuses_everyone_else() -> None:
    with TestClient(_guarded_app()) as client:
        client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.AMBASSADOR))
        assert client.get("/export").status_code == 200

        client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))
        assert client.get("/pipeline").status_code == 200
        denied = client.get("/export")
        assert denied.status_code == 403


def test_a_denial_names_the_permission_that_was_attempted() -> None:
    """ADR-0003 rule 6: a denial that cannot say what was attempted is not evidence."""
    with TestClient(_guarded_app()) as client:
        client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))
        body = client.get("/export").json()
    assert body["code"] == "permission_denied"
    assert body["required_permissions"] == ["export:bulk"]
    assert body["missing_permissions"] == ["export:bulk"]
    assert body["actor_role"] == "TRADE_OFFICER"


def test_a_route_with_no_session_is_refused_before_any_permission_check() -> None:
    with TestClient(_guarded_app()) as client:
        response = client.get("/export")
    assert response.status_code == 403
    assert response.json()["reason"] == "no_session"


def test_require_with_no_permissions_is_a_programming_error() -> None:
    """An empty ``require()`` reads like a check and authorises nothing. Fail at import."""
    with pytest.raises(ValueError, match="at least one permission"):
        require()


# ---------------------------------------------------------------------------
# 5. The second gate: classification
# ---------------------------------------------------------------------------


class _ClassifiedThing:
    """The smallest object satisfying ``SupportsClassification``."""

    def __init__(self, classification: Classification) -> None:
        self.classification = classification


def test_assert_may_read_admits_a_cleared_principal() -> None:
    consular = principal_for_role(RoleCode.CONSULAR_OFFICER)
    assert_may_read(consular, _ClassifiedThing(Classification.CONSULAR_SENSITIVE))


def test_assert_may_read_refuses_an_uncleared_principal() -> None:
    """The demonstrable refusal: a trade officer holding a consular record cannot open it."""
    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    with pytest.raises(ClassificationDeniedError) as excinfo:
        assert_may_read(trade, _ClassifiedThing(Classification.CONSULAR_SENSITIVE))
    assert excinfo.value.extra["classification"] == "CONSULAR_SENSITIVE"
    assert excinfo.value.extra["actor_role"] == "TRADE_OFFICER"


def test_the_two_gates_are_independent() -> None:
    """Holding the permission is not clearance, and clearance is not the permission.

    An ADMIN clears MISSION_INTERNAL yet holds no read permission over its content; a
    TRADE_OFFICER holds plenty of read permissions and is refused a consular zone. Either
    gate alone would give the wrong answer for one of these two.
    """
    admin = principal_for_role(RoleCode.ADMIN)
    trade = principal_for_role(RoleCode.TRADE_OFFICER)

    assert admin.may_read(Classification.MISSION_INTERNAL)
    assert not admin.has(Permission.READ_OPPORTUNITY)

    assert trade.has(Permission.READ_OPPORTUNITY)
    assert not trade.may_read(Classification.CONSULAR_SENSITIVE)


def test_require_classification_gates_a_route() -> None:
    from fastapi import Depends

    app = FastAPI()
    register_exception_handlers(app)

    @app.get(
        "/case",
        dependencies=[Depends(require_classification(Classification.CONSULAR_SENSITIVE))],
    )
    def case() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.CONSULAR_OFFICER))
        assert client.get("/case").status_code == 200

        client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))
        denied = client.get("/case")
        assert denied.status_code == 403
        assert denied.json()["code"] == "classification_denied"


def test_readable_classifications_is_never_empty() -> None:
    """It becomes a ``WHERE ... IN (...)`` clause, which must always be valid SQL."""
    for role in RoleCode:
        assert readable_classifications(principal_for_role(role)), role.value
