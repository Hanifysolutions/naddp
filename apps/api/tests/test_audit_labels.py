"""Every audit action in the closed vocabulary has a plain-language label on the Governance page.

The web app renders the audit log in plain language (``apps/web/src/lib/audit-labels.ts``), and a
new verb added to ``app/audit/actions.py`` without a label would surface as a humanised token on
the tamper-evidence screen. This reads the label map's keys and holds the two to each other in
both directions, the same arrangement ``tests/test_audit_actions.py`` uses for the vocabulary and
the code that writes it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from app.audit.actions import AUDIT_ACTIONS

LABEL_MAP: Final[Path] = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "audit-labels.ts"
)

_KEY: Final[re.Pattern[str]] = re.compile(r"^\s*'([a-z_]+\.[a-z_]+)': \{", re.MULTILINE)


def _labelled_actions() -> set[str]:
    source = LABEL_MAP.read_text(encoding="utf-8")
    block = source.split("export const AUDIT_ACTION_LABELS", 1)[1].split("\n};", 1)[0]
    return set(_KEY.findall(block))


def test_every_action_in_the_vocabulary_has_a_plain_language_label() -> None:
    missing = AUDIT_ACTIONS - _labelled_actions()
    assert not missing, f"label these in {LABEL_MAP.name}: {sorted(missing)}"


def test_every_label_names_an_action_that_exists() -> None:
    stray = _labelled_actions() - AUDIT_ACTIONS
    assert not stray, f"not in app/audit/actions.py: {sorted(stray)}"
