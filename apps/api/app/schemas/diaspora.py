"""Pydantic v2 shapes for ``/v1/diaspora``: how much of the consented directory a caller may search.

The search itself is ``POST /v1/ai/diaspora/match`` and returns the Gateway envelope. This route
exists so the Diaspora screen can state the consent gate in numbers -- how many consented profiles
this caller can search, and how many of those consented to be contacted -- and offer the demo
searches for the role. It returns counts only: no profile, and no count of anyone who has not
consented.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "DiasporaOverviewResponse",
    "DiasporaSelectionRuleResponse",
    "SuggestedSearchResponse",
]


class SuggestedSearchResponse(BaseModel):
    """A demo capability search for this role."""

    id: str
    requirement: str
    expect: Literal["CANDIDATES", "NO_MATCH"] = Field(
        description=(
            "What the consent-gated search is expected to return for this role. Demo data "
            "(data/demo-seed/diaspora_searches.json); the test suite holds the file to it."
        )
    )


class DiasporaSelectionRuleResponse(BaseModel):
    """The rule that turns consented profiles into a candidate set."""

    min_matched_terms: int = Field(description="Key terms a profile must carry (fewer if asked).")
    relative_floor: float = Field(
        description="After the requirement's facets are covered, share of the best score needed."
    )
    max_candidates: int


class DiasporaOverviewResponse(BaseModel):
    """The searchable directory for this caller, in counts."""

    searchable_count: int = Field(
        description=(
            "Consented (directory-only or contactable), not tombstoned, within the caller's "
            "zones. Profiles without consent are never loaded and are not counted here."
        )
    )
    contactable_count: int = Field(description="Of those, consented to be approached.")
    directory_only_count: int = Field(description="Of those, consented to listing only.")
    suggested_searches: list[SuggestedSearchResponse]
    selection: DiasporaSelectionRuleResponse
    measured_at: datetime
