"""The consular case-type taxonomy, read once from ``data/taxonomy/consular_case_types.json``.

Case types are versioned demo data rather than an enum (``app/models/consular.py``), so the
API, the metadata-only triage rules and the dashboard read the same file through this module
instead of each carrying a copy of the SLA budgets. The seed loader keeps its own reader in
``data/demo-seed/seed_parts/registry.py`` because it runs outside the application package;
both read the one file.

``default_sla_days`` is read as **business days** (``docs/OPEN_QUESTIONS.md`` Q-15): the
clock itself is ``app.domain.sla``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Final

from app.core.config import taxonomy_path
from app.domain.enums import Classification

__all__ = ["CASE_TYPES_FILE", "CaseTypeSpec", "case_type", "case_types"]

CASE_TYPES_FILE: Final[str] = "consular_case_types.json"


@dataclass(frozen=True, slots=True)
class CaseTypeSpec:
    """One case type, reduced to what the application uses."""

    code: str
    label: str
    default_sla_days: int
    classification: Classification
    requires_human_determination: bool
    description: str


@cache
def case_types() -> Mapping[str, CaseTypeSpec]:
    """Every case type, keyed by code. Raises at first use if the file is unreadable.

    Failing loudly is the right answer: an SLA budget silently defaulted is a dashboard that
    lies about which cases are late.
    """
    path = taxonomy_path(CASE_TYPES_FILE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Cannot read the consular case-type taxonomy at {path}: {exc}"
        raise RuntimeError(msg) from exc

    entries = document.get("case_types") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        msg = f"{path} has no 'case_types' array."
        raise RuntimeError(msg)

    loaded: dict[str, CaseTypeSpec] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            msg = f"{path}: every case_types entry must be an object."
            raise RuntimeError(msg)
        try:
            spec = CaseTypeSpec(
                code=str(entry["code"]),
                label=str(entry["label"]),
                default_sla_days=int(entry["default_sla_days"]),
                classification=Classification(str(entry["classification"])),
                requires_human_determination=bool(entry["requires_human_determination"]),
                description=str(entry.get("description", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"{path}: malformed case type {entry!r}: {exc}"
            raise RuntimeError(msg) from exc
        loaded[spec.code] = spec
    return MappingProxyType(loaded)


def case_type(code: str | None) -> CaseTypeSpec | None:
    """The spec for ``code``, or ``None`` when the code is absent or unknown."""
    if code is None:
        return None
    return case_types().get(code)
