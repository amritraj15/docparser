from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel


class FieldOut(BaseModel):
    id: str
    field_name: str
    field_value: Optional[str]
    value_type: str
    confidence: float
    source_note: Optional[str]
    is_list_item: bool
    list_item_index: Optional[int]

    class Config:
        from_attributes = True


class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    doc_type: Optional[str]
    doc_type_confidence: Optional[float]
    error_message: Optional[str]
    reused_from_document_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentDetailOut(DocumentOut):
    fields: List[FieldOut] = []

    class Config:
        from_attributes = True


class ReviewItemOut(BaseModel):
    id: str
    document_id: str
    field_id: str
    field_name: str
    original_value: Optional[str]
    current_value: Optional[str]
    confidence: float
    source_note: Optional[str]
    status: str
    corrected_value: Optional[str]

    class Config:
        from_attributes = True


class ReviewResolution(BaseModel):
    action: str  # "confirm" | "correct"
    corrected_value: Optional[str] = None
    reviewer_note: Optional[str] = None


class QueryFilters(BaseModel):
    doc_type: Optional[str] = None
    segment: Optional[str] = None
    system_impacting: Optional[bool] = None
    impact_area: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    text: Optional[str] = None  # freeform substring match across field values


class ExtractionField(BaseModel):
    """Shape the LLM is asked to return per field."""
    value: Optional[Any] = None
    confidence: float
    source_note: Optional[str] = None


class CandidateOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    file_tag: str
    score: float
    snippet: str


class ChangeSuggestionOut(BaseModel):
    id: str
    document_id: str
    target: str
    matched: bool
    reason: Optional[str]
    candidates: List[dict] = []
    status: str
    reviewer_name: Optional[str]
    reviewer_note: Optional[str]
    jira_ticket_key: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChangeSuggestionDetailOut(ChangeSuggestionOut):
    # Denormalized document context, so a PM dashboard doesn't need a second fetch per row.
    document_filename: Optional[str] = None
    circular_number: Optional[str] = None
    segment: Optional[str] = None
    impact_area: Optional[str] = None
    summary: Optional[str] = None


class ChangeSuggestionReview(BaseModel):
    status: str  # "approved" | "needs_discussion" | "rejected" | "pending"
    reviewer_name: Optional[str] = None
    reviewer_note: Optional[str] = None


class JiraTicketDraft(BaseModel):
    title: str
    description: str  # markdown - copy/paste into JIRA, not a live API create (see decisions.md #22)


class JiraTicketKeySave(BaseModel):
    jira_ticket_key: str  # e.g. "KUV-1234", recorded after manual creation for traceability
