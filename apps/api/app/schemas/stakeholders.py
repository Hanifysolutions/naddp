"""Response models for the organisation index and the Stakeholder 360 dossier."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Classification, InteractionType, RelationshipStrength

__all__ = [
    "DossierOpportunityResponse",
    "DossierPersonResponse",
    "DossierResponse",
    "OrganisationListResponse",
    "OrganisationRowResponse",
    "ResolvedSourceResponse",
    "TimelineEntryResponse",
]


class ResolvedSourceResponse(BaseModel):
    """A citation resolved to something the reader can open."""

    model_config = ConfigDict(from_attributes=True)

    citation_id: str = Field(description="Key into data/demo-seed/citations.json.")
    title: str = Field(description="Title of the source document.")
    url: str = Field(description="Real public URL. Never synthesised (BUILD_BIBLE section 11).")
    publisher: str = Field(description="Who published it.")
    verified: bool = Field(
        description="False means TODO_VERIFY: live, but not yet confirmed by eye from this network."
    )
    cited_for: str = Field(description="What this dossier cites the source for.")


class TimelineEntryResponse(BaseModel):
    """One recorded interaction on the dossier timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Interaction id.")
    occurred_at: datetime = Field(description="When it happened.")
    interaction_type: InteractionType = Field(description="Email, call, meeting, event or note.")
    direction: str = Field(description="INBOUND, OUTBOUND or INTERNAL.")
    subject: str = Field(description="One-line subject.")
    body: str = Field(description="The recorded note. Synthetic in this demo.")
    classification: Classification = Field(description="Data zone governing this entry.")
    stakeholder_id: uuid.UUID | None = Field(description="Person involved, if any.")
    stakeholder_name: str | None = Field(description="That person's name, if readable.")
    opportunity_id: uuid.UUID | None = Field(description="Opportunity this was recorded against.")
    opportunity_title: str | None = Field(
        description=(
            "That opportunity's title. Null when the caller may read the interaction but not "
            "the opportunity it points at - the tie is shown, the content is not."
        )
    )
    recorded_by: str | None = Field(description="Officer who recorded it.")


class DossierOpportunityResponse(BaseModel):
    """An opportunity attached to this counterpart."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Opportunity id.")
    title: str = Field(description="Opportunity title.")
    stage: str = Field(description="Current pipeline stage.")
    classification: Classification = Field(description="Data zone governing the opportunity.")
    score: float | None = Field(description="Stored explainable score, 0-100.")
    value_estimate_aud: float | None = Field(description="Estimated value in AUD.")
    next_action_at: datetime | None = Field(description="When the next action falls due.")
    is_proposed_by_ai: bool = Field(description="True for an AI-proposed opportunity (Q-17).")
    link: str = Field(
        description=(
            "How it reached this dossier: 'lead' (lead organisation), 'counterpart' (primary "
            "stakeholder) or 'interaction' (a recorded interaction names it)."
        )
    )


class DossierPersonResponse(BaseModel):
    """A named contact."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Stakeholder id.")
    full_name: str = Field(description="Name.")
    role_title: str = Field(description="Their role at the organisation.")
    influence: str = Field(description="Assessed influence: LOW, MEDIUM or HIGH.")
    relationship_strength: RelationshipStrength = Field(description="Assessed relationship.")
    last_contact_at: datetime | None = Field(description="Most recent recorded contact.")
    email: str | None = Field(description="Contact email. Synthetic in this demo.")
    country: str = Field(description="ISO 3166-1 alpha-2.")
    owner_name: str | None = Field(description="Mission officer who owns the relationship.")


class DossierResponse(BaseModel):
    """Stakeholder 360: everything the mission knows about one counterpart."""

    model_config = ConfigDict(from_attributes=True)

    subject_kind: str = Field(description="'organisation' or 'person'.")
    subject_id: uuid.UUID = Field(description="Id of the subject.")
    name: str = Field(description="Display name.")
    subtitle: str = Field(description="Organisation type, or role and employer for a person.")
    country: str = Field(description="ISO 3166-1 alpha-2.")
    classification: Classification = Field(description="Data zone governing the subject row.")
    description: str = Field(description="What this counterpart is, in a sentence or two.")
    website: str | None = Field(description="Public website, when one is recorded.")
    sectors: list[str] = Field(description="Sector codes this counterpart sits in.")
    people: list[DossierPersonResponse] = Field(description="Named contacts the caller may read.")
    timeline: list[TimelineEntryResponse] = Field(description="Interactions, newest first.")
    opportunities: list[DossierOpportunityResponse] = Field(description="Linked opportunities.")
    sources: list[ResolvedSourceResponse] = Field(description="Citations behind this dossier.")
    strongest_relationship: RelationshipStrength | None = Field(
        description="Strongest relationship held with any readable contact here."
    )
    last_contact_at: datetime | None = Field(description="Most recent readable contact.")
    interaction_count: int = Field(description="Timeline entries returned.")
    dormant: bool = Field(description="True when nothing has been recorded for 90 days.")
    withheld_interactions: int = Field(
        description=(
            "Interactions this caller's clearance removed. A count, never the content - a "
            "silently short timeline would read as a complete one."
        )
    )
    withheld_opportunities: int = Field(description="Linked opportunities removed by clearance.")
    timeline_truncated: bool = Field(description="True when the timeline hit its page limit.")


class OrganisationRowResponse(BaseModel):
    """One line of the organisation index."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Organisation id.")
    name: str = Field(description="Organisation name.")
    org_type: str = Field(description="COMPANY, GOVERNMENT, UNIVERSITY and so on.")
    country: str = Field(description="ISO 3166-1 alpha-2.")
    classification: Classification = Field(description="Data zone governing the row.")
    sectors: list[str] = Field(description="Sector codes.")
    people_count: int = Field(description="Readable contacts at this organisation.")
    interaction_count: int = Field(description="Readable interactions recorded with it.")
    last_contact_at: datetime | None = Field(description="Most recent readable contact.")
    strongest_relationship: RelationshipStrength | None = Field(
        description="Strongest relationship held with any readable contact here."
    )
    opportunity_count: int = Field(description="Readable opportunities it leads.")


class OrganisationListResponse(BaseModel):
    """The organisation index."""

    model_config = ConfigDict(from_attributes=True)

    items: list[OrganisationRowResponse] = Field(description="Organisations, by country then name.")
    total: int = Field(description="How many the caller may read.")
