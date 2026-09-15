"""Postgres ``english`` lexemes for any number of texts, in one round trip.

The lexical half of hybrid retrieval stems with the ``english`` text-search configuration, so
the support tests that decide whether mission records answer a request -- knowledge articles
(``app.services.knowledge``) and diaspora profiles (``app.services.diaspora``) -- use the same
stemming. A term then means the same thing to the ranking and to the test.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sqlalchemy import TextClause, text
from sqlalchemy.orm import Session

__all__ = ["english_lexemes"]

_LEXEMES: Final[TextClause] = text(
    "SELECT tsvector_to_array(to_tsvector('english', item.body)) "
    "FROM unnest(CAST(:bodies AS text[])) WITH ORDINALITY AS item(body, ordinal) "
    "ORDER BY item.ordinal"
)


def english_lexemes(session: Session, texts: Sequence[str]) -> list[frozenset[str]]:
    """One set of stemmed, stop-word-free lexemes per text, in order."""
    if not texts:
        return []
    rows = session.execute(_LEXEMES, {"bodies": list(texts)}).scalars().all()
    return [frozenset(row or ()) for row in rows]
