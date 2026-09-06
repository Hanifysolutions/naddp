# ADR-0003: Real RBAC, faked identity

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Solutions Architect, Lead Engineer
- **Consulted:** Consular stakeholder (demo buyer)
- **Informed:** All contributors
- **Tags:** `security` `governance`

## Context

The demo has to convince a consular audience that access control in NADDP is real, while shipping in
four weeks without an identity-provider integration. `BUILD_BIBLE.md` §3 fixes this as
*"Demo identities (role picker), but RBAC + audit events REAL from day 1"*, and `CLAUDE.md` §2.4 adds
that navigation is generated from permissions and never hard-coded per persona.

The forces:

- **Authentication and authorisation are separable, and only one of them is interesting here.**
  Federating with a mission IdP is weeks of procurement, tenant configuration and consent — and it
  demonstrates nothing about NADDP. Deciding *what a Trade Officer may do with a consular record* is
  the question the buyer actually cares about, and it is entirely independent of how that officer
  proved who they are.
- **Faking the wrong half is fatal.** A demo with real login and fake permissions teaches the codebase
  that permission checks are decoration, and every subsequent feature inherits that assumption. The
  retrofit is enormous. Faking identity leaves exactly one seam to replace later.
- **The demo must be able to show a denial.** `BUILD_BIBLE.md` §6 requires a non-negotiable control to
  be visibly blocked. That is only possible if denials are genuine.
- **Role switching is a demo affordance, not a weakness to hide.** Switching from Trade Officer to
  Consular Officer mid-demo and watching the navigation and the data change is a stronger argument
  than any slide about RBAC.

## Decision

**Authorisation is real.** A role–permission matrix lives in `apps/api/app/security/`, is
**deny-by-default**, and is enforced server-side on every endpoint through a dependency:

```python
def require(permission: Permission, scope: Scope = Scope.GLOBAL) -> Callable: ...
```

Rules:

1. **Deny-by-default.** No matrix entry means denied. An endpoint with no `require(...)` dependency is
   a bug, caught by a test that enumerates the router table and fails on any unguarded route. There is
   no implicit "authenticated users may read" tier.
2. **Server-side, before the query.** List and search endpoints apply the authorisation filter *inside*
   the SQL, not to the result set (`CLAUDE.md` §5). A row the caller may not see is never loaded,
   never counted, and never leaks through a pagination total.
3. **Two-factor decision: permission and classification.** A permission answers "may this role perform
   this verb on this object type"; classification (ADR-0006) answers "may this principal read content
   in this zone". Both must pass. `read:consular_case` does not by itself grant sight of a
   CONSULAR_SENSITIVE attachment.
4. **`bulk_export` is its own permission, on its own axis.** Reading one record and extracting the
   whole table are different risks — the second is the shape of virtually every real data-loss
   incident. `bulk_export` is never implied by any read permission, is granted to few roles, and
   always writes an audit event carrying the row count and the filter used (ADR-0004). Any endpoint
   returning more than a configured page maximum, or a file rather than a JSON page, requires it.
5. **Navigation is derived, not authored.** The web client requests its permission set and renders
   navigation, tiles and actions from it. There is no `if (role === 'AMBASSADOR')` anywhere in
   `apps/web`. A capability that cannot be exercised is not rendered — but the client-side hide is
   cosmetic; the server check is the control, and both exist.
6. **Every denial is recorded.** `policy_result` on the audit row distinguishes `ALLOW` from `DENY`
   (ADR-0004). A security posture that only logs successes cannot show anyone that it works.

**Authentication is faked, and only that.** `POST /v1/session/assume-role` accepts one of six roles —
`AMBASSADOR`, `DEPUTY`, `TRADE_OFFICER`, `CONSULAR_OFFICER`, `DIASPORA_OFFICER`, `ADMIN` — and sets a
cookie `naddp_demo_session`, signed with `itsdangerous` using `DEMO_SESSION_SECRET`. The cookie is
signed (tamper-evident) rather than merely set, so the *shape* of the trust boundary is honest even
though the credential is not: the server never trusts a client-asserted role.

The faked seam is exactly one function — resolving a request to a `Principal`
(`user_id`, `role`, `clearance`, `permissions`). Replacing the role picker with OIDC in the pilot
changes that function and nothing else. No downstream code knows how the principal was established.

**`ADMIN` is a platform-administration role, not a super-user.** It manages users, roles and
configuration, and reads the audit log; it does **not** carry a general content clearance and cannot
read CONSULAR_SENSITIVE case content. Separation of duties is more defensible in front of this
audience than convenience, and it prevents the matrix collapsing into "ADMIN can do everything",
which is the failure mode of most demo RBAC.

## Consequences

### Positive

- The permission matrix is exercised continuously from Week 1 by every developer switching roles,
  so gaps surface early rather than during the security pass.
- VERIFY item 4 (`/v1/command/today` differs between TRADE_OFFICER and CONSULAR_OFFICER) is a real
  test of a real mechanism.
- Winning moment #2 — a consequential action visibly blocked — is a genuine denial path, not a mock.
- IdP integration is removed from the critical path without weakening the security model.
- Derived navigation means adding a role or permission requires no UI change.

### Negative / costs

- Anyone with the demo URL can assume any role. **The deployed demo is, by construction, a public
  system with no authentication.** It must therefore contain only synthetic data
  (`BUILD_BIBLE.md` §11), be labelled DEMO / SYNTHETIC on every screen, and should sit behind a
  deployment-level access gate. This is a real, accepted risk, not an oversight.
- The permission matrix must be authored now, ahead of most features, and will be revised as
  endpoints land. Revisions to a deny-by-default matrix are safe (they fail closed) but are churn.
- Deriving navigation from permissions is more work than a per-persona menu, and produces a slightly
  less curated information architecture per role.
- Two-factor checks (permission *and* classification) mean two places to get right, and two places to
  test. Accepted: collapsing them into one would either over-grant or make classification unusable.

### Neutral / follow-on work

- The exact permission set per role is not fully specified by the Build Bible; the initial matrix is a
  proposal recorded in `docs/OPEN_QUESTIONS.md` for architect sign-off.
- Pilot backlog item: replace `assume-role` with the mission IdP; map IdP groups to the same roles.
- Per-object grants (an officer explicitly added to a CONFIDENTIAL negotiation) are modelled in
  ADR-0006 and are a Week 3+ concern.

## Alternatives considered

### Real authentication (OIDC / Entra ID) in the demo

The most "complete" option. Rejected: it consumes a large share of a four-week budget, depends on a
third party's procurement timeline, and demonstrates nothing NADDP-specific. It is the first item on
the pilot backlog precisely because it is important *and* separable.

### Hard-coded per-persona UI with no server checks

The fastest demo, and the one that would fail the first question a security reviewer asks: "what
happens if I call the API directly?" It also poisons the codebase — every feature built on it assumes
permissions are cosmetic.

### A single "logged in" role with feature flags

Simpler, and it removes the ability to demonstrate a denial. The denial is a headline moment of the
demo, so this is self-defeating.

### ABAC / policy engine (OPA, Cedar) from day one

More expressive, and genuinely the right long-term direction for per-object grants. Rejected for
Week 1: a policy engine is another deployment and another language, and RBAC plus a classification
lattice covers every scenario in the ten-minute script. The `require(permission, scope)` signature is
deliberately shaped so a policy engine can be slotted behind it later.

### Give ADMIN full clearance

Conventional and convenient. Rejected: it undermines the argument being made. A consular audience
reads "the administrator can read every case file" as an unmitigated insider risk.

## Enforcement

| Mechanism | Status |
|---|---|
| Test enumerates every route in the FastAPI router table and fails on any route lacking a `require(...)` dependency (explicit allowlist for `/health` and the OpenAPI schema). | Week 1 |
| Table-driven test over the full role × permission matrix asserting allow/deny for every cell. | Week 1 |
| Test: `bulk_export` is not implied by any read permission for any role. | Week 1 |
| Test: `/v1/command/today` returns materially different payloads for TRADE_OFFICER and CONSULAR_OFFICER. | Week 1 |
| Lint/grep gate in CI: no role-name string literal (`AMBASSADOR`, `CONSULAR_OFFICER`, …) appears in `apps/web/**` outside the role-picker component and generated contract types. | Week 1 |
| Test: a denied request writes an audit row with `policy_result = DENY`. | Week 1 |

## References

- `BUILD_BIBLE.md` §3 (auth row), §6 (non-negotiable controls), §11 (safety rails)
- `CLAUDE.md` §2.4, §5
- `PROMPT_W1_foundation.md` Phase 3, VERIFY item 4
- ADR-0004 (audit), ADR-0006 (classification)
