import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey, JSON, Enum, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_id() -> str:
    return uuid.uuid4().hex


class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    COMPLETE = "complete"
    FAILED = "failed"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=gen_id)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.UPLOADED, nullable=False)
    doc_type = Column(String, nullable=True)          # e.g. "circular", "notice" — set by the classifier
    doc_type_confidence = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    raw_extraction = Column(JSON, nullable=True)       # full LLM response, kept for traceability
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    fields = relationship("ExtractedField", back_populates="document", cascade="all, delete-orphan")


class ExtractedField(Base):
    """
    One row per extracted/classified field. Deliberately flat/EAV-ish (field_name/value)
    rather than a rigid per-doc-type table — see decisions.md for why: it lets the same
    table hold invoice fields, circular classifications, or any future document type
    without a schema migration per type.
    """
    __tablename__ = "extracted_fields"

    id = Column(String, primary_key=True, default=gen_id)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    field_name = Column(String, nullable=False)         # e.g. "segment", "system_impacting"
    field_value = Column(Text, nullable=True)            # stored as text; typed access via helpers
    value_type = Column(String, default="string")        # string | number | date | boolean
    confidence = Column(Float, nullable=False)
    source_note = Column(String, nullable=True)          # e.g. "clause 3, page 2"
    is_list_item = Column(Integer, default=0)             # 1 if this belongs to a repeated group (line items, key points, ...)
    list_item_index = Column(Integer, nullable=True)

    document = relationship("Document", back_populates="fields")


class ReviewItem(Base):
    """
    A queue entry for any extracted field whose confidence fell below the review threshold.
    Kept separate from ExtractedField so the review workflow (status, corrected value,
    reviewer note) doesn't clutter the extraction record itself.
    """
    __tablename__ = "review_items"

    id = Column(String, primary_key=True, default=gen_id)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    field_id = Column(String, ForeignKey("extracted_fields.id"), nullable=False)
    status = Column(Enum(ReviewStatus), default=ReviewStatus.PENDING, nullable=False)
    original_value = Column(Text, nullable=True)   # what the model predicted, preserved even after correction
    corrected_value = Column(Text, nullable=True)
    reviewer_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
