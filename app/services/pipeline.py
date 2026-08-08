import hashlib
import json
import os
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, ExtractedField, ReviewItem, DocumentStatus
from app.services.extraction import (
    extract_document, ExtractionError, contract_fingerprint, current_model_identifier,
)


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
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _find_reusable_source(db: Session, doc: Document) -> Optional[Document]:
    """
    Looks for a prior document with identical PDF bytes, processed under the exact same
    provider + model + prompt/schema fingerprint, that finished successfully (not FAILED —
    a failed prior attempt isn't something to reuse as if it succeeded). Trade-off:
    byte-identical match only — a re-scanned or re-saved copy of the same physical document
    produces different bytes and won't match. Catching that would mean hashing extracted
    text instead of raw bytes, which is real scope, not built here (decisions.md #26).
    """
    if not doc.content_hash:
        return None
    return (
        db.query(Document)
        .filter(
            Document.id != doc.id,
            Document.content_hash == doc.content_hash,
            Document.extraction_provider == settings.llm_provider,
            Document.extraction_model == current_model_identifier(),
            Document.extraction_contract_fingerprint == contract_fingerprint(),
            Document.status.in_([DocumentStatus.COMPLETE, DocumentStatus.NEEDS_REVIEW]),
        )
        .order_by(Document.created_at.desc())
        .first()
    )


def _copy_fields_from(db: Session, source: Document, target: Document) -> bool:
    """
    Copies a source document's ORIGINAL extracted fields (never its human corrections —
    see decisions.md #26 on why) onto the target document, creating fresh, independent
    ReviewItems for anything below threshold. Returns True if the target ends up needing
    review, same contract as the fresh-extraction path in process_document().
    """
    any_needs_review = source.doc_type_confidence < settings.review_confidence_threshold

    for src_field in source.fields:
        row = ExtractedField(
            document_id=target.id,
            field_name=src_field.field_name,
            field_value=src_field.field_value,
            value_type=src_field.value_type,
            confidence=src_field.confidence,
            source_note=src_field.source_note,
            is_list_item=src_field.is_list_item,
            list_item_index=src_field.list_item_index,
        )
        db.add(row)
        db.flush()

        if src_field.confidence < settings.review_confidence_threshold:
            any_needs_review = True
            db.add(ReviewItem(document_id=target.id, field_id=row.id, original_value=row.field_value))

    return any_needs_review


def process_document(db: Session, document_id: str, force: bool = False) -> Document:
    """
    Runs extraction for a document and persists the results. Never raises past this
    boundary in normal operation: a failure lands the document in FAILED status with
    error_message set, so a single bad file can't take down a batch of uploads.

    If force=False (the default, used right after upload) and an identical PDF was already
    processed successfully under the exact same provider/model/prompt-schema fingerprint,
    this reuses that result instead of spending a new LLM call — see _find_reusable_source.
    /documents/{id}/reprocess always calls this with force=True, bypassing reuse entirely,
    so "reprocess anyway" is always available even when a cached match exists.
    """
    doc = db.get(Document, document_id)
    if doc is None:
        raise ValueError(f"No document with id {document_id}")

    doc.status = DocumentStatus.PROCESSING
    db.commit()

    if not force:
        source = _find_reusable_source(db, doc)
        if source is not None:
            doc.doc_type = source.doc_type
            doc.doc_type_confidence = source.doc_type_confidence
            doc.raw_extraction = source.raw_extraction
            doc.reused_from_document_id = source.id
            doc.extraction_provider = source.extraction_provider
            doc.extraction_model = source.extraction_model
            doc.extraction_contract_fingerprint = source.extraction_contract_fingerprint

            any_needs_review = _copy_fields_from(db, source, doc)
            doc.status = DocumentStatus.NEEDS_REVIEW if any_needs_review else DocumentStatus.COMPLETE
            db.commit()
            db.refresh(doc)
            return doc

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
    doc.reused_from_document_id = None  # a forced reprocess always produces a fresh result
    doc.extraction_provider = settings.llm_provider
    doc.extraction_model = current_model_identifier()
    doc.extraction_contract_fingerprint = contract_fingerprint()
    if result.extraction_notes:
        doc.error_message = None

    # A forced reprocess replaces this document's own fields entirely - clear the old ones
    # (the router already does this before calling reprocess, but guard here too in case
    # process_document is ever called directly with force=True from elsewhere).
    if force:
        for f in list(doc.fields):
            db.delete(f)
        db.flush()

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
