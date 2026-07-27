import json
import os
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, ExtractedField, ReviewItem, DocumentStatus
from app.services.extraction import extract_document, ExtractionError


def save_upload(db: Session, filename: str, content_type: str, raw_bytes: bytes) -> Document:
    os.makedirs(settings.upload_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{os.path.basename(filename)}"
    file_path = os.path.join(settings.upload_dir, safe_name)
    with open(file_path, "wb") as f:
        f.write(raw_bytes)

    doc = Document(
        filename=filename,
        file_path=file_path,
        content_type=content_type,
        status=DocumentStatus.UPLOADED,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def process_document(db: Session, document_id: str) -> Document:
    """
    Runs extraction for a document and persists the results. Never raises past this
    boundary in normal operation: a failure lands the document in FAILED status with
    error_message set, so a single bad file can't take down a batch of uploads.
    """
    doc = db.get(Document, document_id)
    if doc is None:
        raise ValueError(f"No document with id {document_id}")

    doc.status = DocumentStatus.PROCESSING
    db.commit()

    try:
        result = extract_document(doc.file_path)
    except ExtractionError as e:
        doc.status = DocumentStatus.FAILED
        doc.error_message = str(e)
        db.commit()
        db.refresh(doc)
        return doc

    doc.doc_type = result.doc_type
    doc.doc_type_confidence = result.doc_type_confidence
    doc.raw_extraction = result.raw
    if result.extraction_notes:
        doc.error_message = None

    any_needs_review = result.doc_type_confidence < settings.review_confidence_threshold

    for f in result.fields:
        row = ExtractedField(
            document_id=doc.id,
            field_name=f.field_name,
            field_value=None if f.value is None else str(f.value),
            value_type=_infer_type(f.value),
            confidence=f.confidence,
            source_note=f.source_note,
            is_list_item=1 if f.is_list_item else 0,
            list_item_index=f.list_item_index,
        )
        db.add(row)
        db.flush()  # get row.id before creating a possible review item

        if f.confidence < settings.review_confidence_threshold:
            any_needs_review = True
            db.add(ReviewItem(document_id=doc.id, field_id=row.id, original_value=row.field_value))

    doc.status = DocumentStatus.NEEDS_REVIEW if any_needs_review else DocumentStatus.COMPLETE
    db.commit()
    db.refresh(doc)
    return doc


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"
