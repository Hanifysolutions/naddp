"""``Principal`` -- the resolved identity a request acts under, and the ADR-0006 read rule.

A :class:`Principal` is what a request resolves to *after* identity has been established.
It carries everything both authorisation gates need and nothing else: the permission set
(gate 1, ``app.security.matrix``) and the clearance rank plus compartments (gate 2,
ADR-0006). Downstream code never learns how the principal was established, which is the
whole point of ADR-0003: replacing the demo role picker with an identity provider changes
:func:`principal_for_role` and nothing else.

**Ranks and compartments are read from ``data/taxonomy/classifications.json`` at import,
never hard-coded here.** That file is the machine-readable definition ADR-0006 names as
the single source, it is also what the seed loader writes into the ``roles`` table, and
the whole argument of the ADR collapses if two copies of the numbers can disagree. Import
fails loudly if the file is missing, malformed, or does not cover all six roles and all
four zones -- a security module that silently degrades to a default is worse than one that
refuses to start.

The demo personas live here too (:data:`DEMO_PERSONAS`), because the audit log needs a
real ``users`` row to point at and that row's identity must be derivable from the role
alone. Their ids are deterministic UUIDv5 values, so the seed loader, the API and a test
all mint the same six users without coordinating.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from app.core.config import taxonomy_path
from app.domain.enums import Classification, RoleCode
from app.security.matrix import ROLE_PERMISSIONS
from app.security.permissions import Permission

__all__ = [
    "CLASSIFICATION_ACCESS",
    "CONSULAR_COMPARTMENT",
    "DEMO_PERSONAS",
    "DEMO_PERSONA_NAMESPACE",
    "ROLE_CLEARANCE_RANK",
    "ROLE_COMPARTMENTS",
    "ClassificationAccess",
    "DemoPersona",
    "Principal",
    "demo_persona",
    "principal_for_role",
    "readable_classifications_for",
]

#: The one compartment in the system (ADR-0006). Named rather than spelled inline so a
#: typo is an ImportError instead of a silent grant of nothing to nobody.
CONSULAR_COMPARTMENT: Final[str] = "consular"

_TAXONOMY_FILE: Final[Path] = taxonomy_path("classifications.json")


@dataclass(frozen=True, slots=True)
class ClassificationAccess:
    """The read rule for one zone, as stated in ``classifications.json``.

    ``rank`` is the *dominance* rank used for propagation (``max`` over the parts of an
    aggregate) and is deliberately not the access order: ``CONSULAR_SENSITIVE`` ranks
    below ``CONFIDENTIAL`` yet is harder to reach, because it additionally requires a
    compartment. Keeping the two apart is what stops "more senior" from quietly becoming
    "may read everything".
    """

    zone: Classification
    rank: int
    min_role_rank_to_read: int
    compartment: str | None
    display_label: str


def _load_taxonomy() -> dict[str, Any]:
    """Read and minimally shape-check ``data/taxonomy/classifications.json``."""
    try:
        raw = _TAXONOMY_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        msg = (
            f"Cannot read the classification taxonomy at {_TAXONOMY_FILE}. It is the "
            "single source for clearance ranks and compartments (ADR-0006); the security "
            "layer must not start without it."
        )
        raise RuntimeError(msg) from exc

    try:
        document: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{_TAXONOMY_FILE} is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc

    if not isinstance(document, dict):
        msg = f"{_TAXONOMY_FILE} must contain a JSON object at the top level."
        raise RuntimeError(msg)
    return document


def _load_role_clearance(
    document: dict[str, Any],
) -> tuple[
    Mapping[RoleCode, int],
    Mapping[RoleCode, frozenset[str]],
]:
    """Build the rank and compartment tables, asserting all six roles are covered."""
    entries = document.get("role_ranks")
    if not isinstance(entries, list):
        msg = f"{_TAXONOMY_FILE} has no 'role_ranks' array."
        raise RuntimeError(msg)

    ranks: dict[RoleCode, int] = {}
    compartments: dict[RoleCode, frozenset[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            msg = f"{_TAXONOMY_FILE}: every role_ranks entry must be an object."
            raise RuntimeError(msg)
        try:
            role = RoleCode(entry["role"])
        except (KeyError, ValueError) as exc:
            msg = f"{_TAXONOMY_FILE}: unknown or missing role in role_ranks ({entry!r})."
            raise RuntimeError(msg) from exc
        rank = entry.get("clearance_rank")
        if not isinstance(rank, int):
            msg = f"{_TAXONOMY_FILE}: {role.value} has a non-integer clearance_rank."
            raise RuntimeError(msg)
        held = entry.get("compartments", [])
        if not isinstance(held, list) or not all(isinstance(item, str) for item in held):
            msg = f"{_TAXONOMY_FILE}: {role.value} compartments must be a list of strings."
            raise RuntimeError(msg)
        ranks[role] = rank
        compartments[role] = frozenset(str(item) for item in held)

    missing = set(RoleCode) - set(ranks)
    if missing:
        names = ", ".join(sorted(role.value for role in missing))
        msg = f"{_TAXONOMY_FILE} is missing a clearance rank for: {names}."
        raise RuntimeError(msg)

    return MappingProxyType(ranks), MappingProxyType(compartments)


def _load_classification_access(
    document: dict[str, Any],
) -> Mapping[Classification, ClassificationAccess]:
    """Build the per-zone read rule, asserting all four zones are covered."""
    entries = document.get("classifications")
    if not isinstance(entries, list):
        msg = f"{_TAXONOMY_FILE} has no 'classifications' array."
        raise RuntimeError(msg)

    access: dict[Classification, ClassificationAccess] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            msg = f"{_TAXONOMY_FILE}: every classifications entry must be an object."
            raise RuntimeError(msg)
        try:
            zone = Classification(entry["code"])
        except (KeyError, ValueError) as exc:
            msg = f"{_TAXONOMY_FILE}: unknown or missing classification code ({entry!r})."
            raise RuntimeError(msg) from exc
        rank = entry.get("rank")
        min_rank = entry.get("min_role_rank_to_read")
        if not isinstance(rank, int) or not isinstance(min_rank, int):
            msg = f"{_TAXONOMY_FILE}: {zone.value} needs integer rank and min_role_rank_to_read."
            raise RuntimeError(msg)
        compartment = entry.get("compartment")
        if compartment is not None and not isinstance(compartment, str):
            msg = f"{_TAXONOMY_FILE}: {zone.value} compartment must be a string or null."
            raise RuntimeError(msg)
        label = entry.get("display_label")
        access[zone] = ClassificationAccess(
            zone=zone,
            rank=rank,
            min_role_rank_to_read=min_rank,
            compartment=compartment,
            display_label=label if isinstance(label, str) else zone.value,
        )

    missing = set(Classification) - set(access)
    if missing:
        names = ", ".join(sorted(zone.value for zone in missing))
        msg = f"{_TAXONOMY_FILE} is missing an access rule for: {names}."
        raise RuntimeError(msg)

    return MappingProxyType(access)


#: The taxonomy document, read exactly once at import.
_TAXONOMY: Final[dict[str, Any]] = _load_taxonomy()

_ROLE_TABLES: Final[tuple[Mapping[RoleCode, int], Mapping[RoleCode, frozenset[str]]]] = (
    _load_role_clearance(_TAXONOMY)
)

#: Clearance rank per role: AMBASSADOR 40, DEPUTY 30, the three officer roles 20, ADMIN 10.
ROLE_CLEARANCE_RANK: Final[Mapping[RoleCode, int]] = _ROLE_TABLES[0]

#: Compartments per role. Only AMBASSADOR, DEPUTY and CONSULAR_OFFICER hold ``consular``.
ROLE_COMPARTMENTS: Final[Mapping[RoleCode, frozenset[str]]] = _ROLE_TABLES[1]

#: The ADR-0006 read rule per zone, straight from the taxonomy file.
CLASSIFICATION_ACCESS: Final[Mapping[Classification, ClassificationAccess]] = (
    _load_classification_access(_TAXONOMY)
)


# ---------------------------------------------------------------------------
# Demo personas
# ---------------------------------------------------------------------------

#: Namespace for the deterministic persona ids. A fixed UUID, never regenerated.
#:
#: The demo has no identity provider, so a principal's ``user_id`` must be derivable from
#: its role alone -- ``get_principal`` sees a cookie and nothing else. A UUIDv5 over this
#: namespace and the role code gives the API, the seed loader and every test the same six
#: ids without coordinating, so ``audit_events.actor_user_id`` always resolves to a real
#: ``users`` row (the foreign key is ON DELETE RESTRICT and will say so if it does not).
#:
#: These ids are NOT ULIDs and deliberately break the ADR-0007 convention that primary
#: keys are time-sortable. That is correct here: a persona is fixed reference data whose
#: identity must survive a re-seed, and there is no creation instant worth encoding.
DEMO_PERSONA_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6f6d5f4e-1a2b-5c3d-8e9f-0a1b2c3d4e5f")


@dataclass(frozen=True, slots=True)
class DemoPersona:
    """The synthetic staff member behind one role (``BUILD_BIBLE.md`` section 11).

    Every field is invented. No real person, address or mission contact appears here, and
    ``@naddp.demo`` is a reserved, non-routable domain so a stray email can never leave.
    """

    role: RoleCode
    user_id: uuid.UUID
    email: str
    full_name: str
    title: str
    mission: str


_MISSION: Final[str] = "Nigerian High Commission, Canberra"


def _persona(role: RoleCode, full_name: str, title: str, local_part: str) -> DemoPersona:
    """Build one persona with a deterministic id derived from its role code."""
    return DemoPersona(
        role=role,
        user_id=uuid.uuid5(DEMO_PERSONA_NAMESPACE, role.value),
        email=f"{local_part}@naddp.demo",
        full_name=full_name,
        title=title,
        mission=_MISSION,
    )


#: The six demo identities the role picker offers. Synthetic, and badged as such.
DEMO_PERSONAS: Final[Mapping[RoleCode, DemoPersona]] = MappingProxyType(
    {
        RoleCode.AMBASSADOR: _persona(
            RoleCode.AMBASSADOR, "Amara Obi", "High Commissioner", "amara.obi"
        ),
        RoleCode.DEPUTY: _persona(
            RoleCode.DEPUTY, "Tunde Bakare", "Deputy Head of Mission", "tunde.bakare"
        ),
        RoleCode.TRADE_OFFICER: _persona(
            RoleCode.TRADE_OFFICER,
            "Ngozi Eze",
            "Senior Trade Commissioner",
            "ngozi.eze",
        ),
        RoleCode.CONSULAR_OFFICER: _persona(
            RoleCode.CONSULAR_OFFICER, "Ifeoma Adeyemi", "Consular Officer", "ifeoma.adeyemi"
        ),
        RoleCode.DIASPORA_OFFICER: _persona(
            RoleCode.DIASPORA_OFFICER,
            "Chidi Nwosu",
            "Diaspora Engagement Officer",
            "chidi.nwosu",
        ),
        RoleCode.ADMIN: _persona(
            RoleCode.ADMIN, "Sade Balogun", "Platform Administrator", "sade.balogun"
        ),
    }
)


def demo_persona(role: RoleCode) -> DemoPersona:
    """Return the demo persona for ``role``.

    Raises:
        KeyError: if a role has no persona. The mapping is total over :class:`RoleCode`
            and a test asserts it.
    """
    return DEMO_PERSONAS[role]


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Principal:
    """The identity, capabilities and clearance a request acts under.

    Frozen and hashable: a principal is a *decision already made*, resolved once per
    request. Nothing downstream may add a permission or raise a clearance, and code that
    tries fails immediately rather than in review. This is what lets a principal be passed
    into a service, a query builder and the audit writer without any of them being able to
    widen it.

    ``permissions`` answers "may this role perform this verb on this object type";
    ``clearance_rank`` and ``compartments`` answer "may this principal read content in
    this zone". Both gates must pass, and neither implies the other (ADR-0003 rule 3).
    """

    user_id: uuid.UUID
    email: str
    full_name: str
    role: RoleCode
    permissions: frozenset[Permission]
    clearance_rank: int
    compartments: frozenset[str]

    def has(self, permission: Permission) -> bool:
        """Return whether this principal holds ``permission``. Deny-by-default."""
        return permission in self.permissions

    def has_all(self, permissions: frozenset[Permission]) -> bool:
        """Return whether this principal holds every one of ``permissions``."""
        return permissions <= self.permissions

    def missing(self, permissions: frozenset[Permission]) -> frozenset[Permission]:
        """Return the subset of ``permissions`` this principal does not hold.

        Returned rather than merely counted so a denial can name what was attempted:
        every denial is audited (ADR-0003 rule 6), and "denied" without "denied *what*"
        is not evidence of anything.
        """
        return permissions - self.permissions

    def may_read(self, classification: Classification) -> bool:
        """Return whether this principal is cleared for ``classification`` (ADR-0006).

        The rule, verbatim from ``data/taxonomy/classifications.json``::

            clearance_rank >= zone.min_role_rank_to_read
            AND (zone.compartment is None OR zone.compartment in compartments)

        The compartment half is why an ``AMBASSADOR`` at rank 40 reads a consular case
        (holds ``consular``) while an ``ADMIN`` at rank 10 does not, and why a
        ``CONSULAR_OFFICER`` at rank 20 reads a case but not a ``CONFIDENTIAL``
        negotiating position. Seniority alone never opens the case file.

        Per-object grants -- the third clause of the ADR-0006 rule, and the pressure valve
        that keeps ranks from inflating -- are a Week 3+ concern. They belong inside this
        method when they land, so that every caller inherits them at once; that is the
        reason the rule is written as one function rather than inlined at call sites.
        """
        rule = CLASSIFICATION_ACCESS[classification]
        if self.clearance_rank < rule.min_role_rank_to_read:
            return False
        return rule.compartment is None or rule.compartment in self.compartments

    @property
    def is_demo_identity(self) -> bool:
        """True while identity is faked by the role picker (ADR-0003).

        Hard-coded rather than computed: every principal in this build is a demo identity,
        and the flag exists so the UI can badge it and so the value stops being a constant
        the day real authentication lands.
        """
        return True


def principal_for_role(role: RoleCode) -> Principal:
    """Build the :class:`Principal` for ``role``.

    **This function is the faked seam, and it is the only one** (ADR-0003). Everything it
    returns -- the permission set, the clearance rank, the compartments -- is real and is
    read from the authoritative sources. Only the answer to "who is this person" is
    invented, and it is invented deterministically so the audit log can point at a stable
    ``users`` row. Replacing the role picker with OIDC means rewriting this function body
    to map an IdP subject and its groups onto the same four sources; no caller changes.
    """
    persona = DEMO_PERSONAS[role]
    return Principal(
        user_id=persona.user_id,
        email=persona.email,
        full_name=persona.full_name,
        role=role,
        permissions=ROLE_PERMISSIONS[role],
        clearance_rank=ROLE_CLEARANCE_RANK[role],
        compartments=ROLE_COMPARTMENTS[role],
    )


def readable_classifications_for(principal: Principal) -> list[Classification]:
    """Return every zone ``principal`` may read, in ascending dominance rank.

    The list a list/search query turns into its ``WHERE classification IN (...)`` clause.
    Ordered deterministically so a generated SQL string is stable and diffable, and so a
    cached query plan is not defeated by set iteration order.

    See ``app.security.deps.readable_classifications`` for the dependency-injected form
    route handlers use; this is the plain function, importable without FastAPI.
    """
    return sorted(
        (zone for zone in Classification if principal.may_read(zone)),
        key=lambda zone: CLASSIFICATION_ACCESS[zone].rank,
    )
