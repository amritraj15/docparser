from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ReviewItem, ExtractedField, Document, ReviewStatus, DocumentStatus
from app.schemas import ReviewItemOut, ReviewResolution

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/queue", response_model=List[ReviewItemOut])
def get_review_queue(
    status: Optional[str] = "pending",
    db: Session = Depends(get_db),
):
    q = db.query(ReviewItem, ExtractedField).join(
        ExtractedField, ReviewItem.field_id == ExtractedField.id
    )
    if status:
        q = q.filter(ReviewItem.status == status)

    out = []
    for review, extracted_field in q.order_by(ReviewItem.created_at.asc()).all():
        out.append(ReviewItemOut(
            id=review.id,
            document_id=review.document_id,
            field_id=review.field_id,
            field_name=extracted_field.field_name,
            original_value=review.original_value,
            current_value=extracted_field.field_value,
            confidence=extracted_field.confidence,
            source_note=extracted_field.source_note,
            status=review.status,
            corrected_value=review.corrected_value,
        ))
    return out


@router.post("/{review_id}/resolve", response_model=ReviewItemOut)
def resolve_review_item(review_id: str, resolution: ReviewResolution, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review item not found.")

    extracted_field = db.get(ExtractedField, review.field_id)

    if resolution.action == "confirm":
        review.status = ReviewStatus.CONFIRMED
    elif resolution.action == "correct":
        if resolution.corrected_value is None:
            raise HTTPException(status_code=400, detail="corrected_value is required for action=correct.")
        review.status = ReviewStatus.CORRECTED
        review.corrected_value = resolution.corrected_value
        extracted_field.field_value = resolution.corrected_value
        extracted_field.confidence = 1.0  # human-confirmed value is treated as ground truth
    else:
        raise HTTPException(status_code=400, detail="action must be 'confirm' or 'correct'.")

    review.reviewer_note = resolution.reviewer_note
    review.resolved_at = datetime.utcnow()
    db.commit()

    _maybe_close_out_document(db, review.document_id)

    db.refresh(review)
    return ReviewItemOut(
        id=review.id,
        document_id=review.document_id,
        field_id=review.field_id,
        field_name=extracted_field.field_name,
        original_value=review.original_value,
        current_value=extracted_field.field_value,
        confidence=extracted_field.confidence,
        source_note=extracted_field.source_note,
        status=review.status,
        corrected_value=review.corrected_value,
    )


def _maybe_close_out_document(db: Session, document_id: str) -> None:
    """Once every review item for a document is resolved, promote it out of NEEDS_REVIEW."""
    remaining_pending = db.query(ReviewItem).filter(
        ReviewItem.document_id == document_id,
        ReviewItem.status == ReviewStatus.PENDING,
    ).count()

    if remaining_pending == 0:
        doc = db.get(Document, document_id)
        if doc and doc.status == DocumentStatus.NEEDS_REVIEW:
            doc.status = DocumentStatus.COMPLETE
            db.commit()
