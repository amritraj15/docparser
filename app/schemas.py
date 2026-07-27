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
