"""Text normalisation shared by embedding and ingestion.

Lives in ``core`` because both sides of an ADR-0001 boundary need it: ``app.ai``
embeds normalised text and ``app.services.ingestion`` hashes it, and a service may not
import ``app.ai``. Putting it here is what keeps the dedupe hash and the vector talking
about the same string without creating that dependency edge.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalise_text"]


def normalise_text(raw: str) -> str:
    """Canonical form used for both hashing and embedding.

    Ingestion hashes the *normalised* text, so a document that differs only in whitespace,
    Unicode composition or smart quotes is correctly recognised as unchanged and is not
    re-embedded. Normalising in one place is what keeps the dedupe hash and the vector
    talking about the same string.
    """
    text = unicodedata.normalize("NFKC", raw)
    # Ruff flags these as ambiguous unicode, which is exactly what they are - mapping
    # them to ASCII is this function's job. Written as escapes so the source itself
    # stays unambiguous to a reader and to the linter.
    text = text.replace("\u2019", "'").replace("\u2018", "'")  # curly single quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # curly double quotes
    text = text.replace("\u2013", "-").replace("\u2014", "-")  # en dash, em dash
    text = text.replace("\u00a0", " ")  # non-breaking space
    return re.sub(r"[ \t]+", " ", text).strip()
