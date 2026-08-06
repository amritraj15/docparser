from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, SessionLocal
from app.models import Document, DocumentStatus, ChangeSuggestion, ChangeSuggestionStatus
from app.schemas import DocumentOut, DocumentDetailOut, ChangeSuggestionOut
from app.services.pipeline import save_upload, process_document
from app.services.repo_index import search as repo_search, RepoIndexError


def _process_document_isolated(document_id: str) -> None:
    """
    Background tasks must not reuse the request-scoped session (FastAPI may close it
    before the task runs) — open a fresh one for the duration of the job.
    """
    db = SessionLocal()
    try:
        process_document(db, document_id)
    finally:
        db.close()

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_CONTENT_TYPES = {"application/pdf"}


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported content type '{file.content_type}'. Only application/pdf is accepted.",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds max size of {settings.max_upload_bytes} bytes.",
        )

    doc = save_upload(db, filename=file.filename, content_type=file.content_type, raw_bytes=raw_bytes)

    # Extraction runs out-of-request so a slow/failed LLM call never blocks the upload
    # response. Swap this for a real queue (Celery/RQ/arq) beyond single-instance demo scale.
    background_tasks.add_task(_process_document_isolated, doc.id)

    return doc


@router.get("", response_model=List[DocumentOut])
def list_documents(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Document)
    if status:
        q = q.filter(Document.status == status)
    return q.order_by(Document.created_at.desc()).all()


@router.get("/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
def reprocess_document(document_id: str, db: Session = Depends(get_db)):
    """Re-run extraction, e.g. after a transient LLM API failure marked the doc FAILED."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    db.query(Document).filter(Document.id == document_id)  # no-op, keeps symmetry with delete-style ops
    for f in list(doc.fields):
        db.delete(f)
    db.commit()

    return process_document(db, document_id)


def _field_value(doc: Document, name: str) -> Optional[str]:
    for f in doc.fields:
        if f.field_name == name and not f.is_list_item:
            return f.field_value
    return None


@router.post("/{document_id}/suggest-changes", response_model=List[ChangeSuggestionOut])
def suggest_changes(document_id: str, db: Session = Depends(get_db)):
    """
    For a system-impacting circular, retrieves candidate code locations from a local,
    pre-indexed codebase (see /repo-index) and PERSISTS the result as ChangeSuggestion
    row(s) — one per target repo — so a PM/lead can review it later via /change-suggestions,
    not just see it once in this response. Re-running this while a suggestion is still
    `pending` refreshes it (the repo may have been re-indexed); once a PM has moved it past
    pending (approved/rejected/needs_discussion), re-running does NOT silently overwrite
    their decision — that would erase a real review outcome.

    Disabled unless REPO_SUGGESTION_ENABLED=true — see repo_index.py for why this stays off
    by default and never touches a cloud API.
    """
    if not settings.repo_suggestion_enabled:
        raise HTTPException(
            status_code=403,
            detail="Repo change-suggestion is disabled (REPO_SUGGESTION_ENABLED=false).",
        )

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    system_impacting = _field_value(doc, "system_impacting")
    if str(system_impacting).strip().lower() not in ("true", "1", "yes"):
        raise HTTPException(
            status_code=400,
            detail="This document isn't classified as system_impacting — nothing to suggest.",
        )

    impact_area = (_field_value(doc, "impact_area") or "").lower()
    targets = {"backend": ["backend"], "frontend": ["frontend"], "both": ["backend", "frontend"]}.get(impact_area)
    if not targets:
        raise HTTPException(
            status_code=400,
            detail=f"impact_area '{impact_area}' doesn't map to an indexed codebase (backend/frontend/both).",
        )

    key_points = [f.field_value for f in doc.fields if f.field_name == "key_point" and f.field_value]
    summary = _field_value(doc, "summary")
    query_text = "\n".join(key_points) or summary or _field_value(doc, "subject") or ""
    if not query_text.strip():
        raise HTTPException(status_code=400, detail="Document has no summary or key points to search with.")

    out = []
    for target in targets:
        existing = (
            db.query(ChangeSuggestion)
            .filter(ChangeSuggestion.document_id == document_id, ChangeSuggestion.target == target)
            .first()
        )
        if existing is not None and existing.status != ChangeSuggestionStatus.PENDING:
            # Already reviewed - don't silently overwrite a PM/lead's decision.
            out.append(existing)
            continue

        try:
            result = repo_search(target, query_text)
        except RepoIndexError as e:
            result = {"matched": False, "reason": str(e), "candidates": []}

        if existing is None:
            existing = ChangeSuggestion(document_id=document_id, target=target)
            db.add(existing)
        existing.matched = 1 if result["matched"] else 0
        existing.reason = result.get("reason")
        existing.candidates = result.get("candidates") or []
        db.flush()
        out.append(existing)

    db.commit()
    for s in out:
        db.refresh(s)
    return out
