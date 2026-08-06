from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ChangeSuggestion, ChangeSuggestionStatus, Document
from app.schemas import (
    ChangeSuggestionOut, ChangeSuggestionDetailOut, ChangeSuggestionReview,
    JiraTicketDraft, JiraTicketKeySave,
)

router = APIRouter(prefix="/change-suggestions", tags=["change-suggestions"])

_VALID_STATUSES = {s.value for s in ChangeSuggestionStatus}


def _field_value(doc: Document, name: str) -> Optional[str]:
    for f in doc.fields:
        if f.field_name == name and not f.is_list_item:
            return f.field_value
    return None


def _to_detail(s: ChangeSuggestion) -> ChangeSuggestionDetailOut:
    doc = s.document
    return ChangeSuggestionDetailOut(
        id=s.id,
        document_id=s.document_id,
        target=s.target,
        matched=bool(s.matched),
        reason=s.reason,
        candidates=s.candidates or [],
        status=s.status,
        reviewer_name=s.reviewer_name,
        reviewer_note=s.reviewer_note,
        jira_ticket_key=s.jira_ticket_key,
        created_at=s.created_at,
        updated_at=s.updated_at,
        document_filename=doc.filename if doc else None,
        circular_number=_field_value(doc, "circular_number") if doc else None,
        segment=_field_value(doc, "segment") if doc else None,
        impact_area=_field_value(doc, "impact_area") if doc else None,
        summary=_field_value(doc, "summary") if doc else None,
    )


@router.get("", response_model=List[ChangeSuggestionDetailOut])
def list_change_suggestions(
    status: Optional[str] = None,
    matched_only: bool = True,
    db: Session = Depends(get_db),
):
    """
    The PM/lead review dashboard's data source. Defaults to matched suggestions only
    (matched_only=True) — an unmatched "likely new functionality" result isn't something
    to route into a JIRA-ticket review flow the same way a concrete file suggestion is;
    pass matched_only=false to see those too.
    """
    q = db.query(ChangeSuggestion).options(joinedload(ChangeSuggestion.document).joinedload(Document.fields))
    if status:
        if status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}.")
        q = q.filter(ChangeSuggestion.status == status)
    if matched_only:
        q = q.filter(ChangeSuggestion.matched == 1)

    rows = q.order_by(ChangeSuggestion.created_at.desc()).all()
    return [_to_detail(s) for s in rows]


@router.get("/{suggestion_id}", response_model=ChangeSuggestionDetailOut)
def get_change_suggestion(suggestion_id: str, db: Session = Depends(get_db)):
    s = db.get(ChangeSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Change suggestion not found.")
    return _to_detail(s)


@router.post("/{suggestion_id}/review", response_model=ChangeSuggestionDetailOut)
def review_change_suggestion(suggestion_id: str, body: ChangeSuggestionReview, db: Session = Depends(get_db)):
    """
    Records a PM/lead's decision on a suggestion: approved (create a JIRA task), needs
    discussion (flag for a sync conversation), or rejected (no action needed). This is
    what makes the review durable — the same suggestion can be revisited, discussed, and
    the outcome tracked, rather than a one-shot API response nobody can act on later.
    """
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}.")

    s = db.get(ChangeSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Change suggestion not found.")

    s.status = body.status
    if body.reviewer_name is not None:
        s.reviewer_name = body.reviewer_name
    if body.reviewer_note is not None:
        s.reviewer_note = body.reviewer_note
    db.commit()
    db.refresh(s)
    return _to_detail(s)


@router.get("/{suggestion_id}/jira-ticket", response_model=JiraTicketDraft)
def generate_jira_ticket_draft(suggestion_id: str, db: Session = Depends(get_db)):
    """
    Generates a formatted JIRA ticket draft (title + Markdown description) for a PM to
    copy into JIRA directly. This is deliberately NOT a live JIRA API integration — see
    decisions.md #22 for why: it would need real JIRA credentials this project has never
    had access to, and shipping an untested API integration is exactly the mistake this
    project already made once with an unverified OpenRouter model alias. A copy-paste
    draft is honest about what's actually been built and verified.
    """
    s = db.get(ChangeSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Change suggestion not found.")
    doc = s.document
    if doc is None:
        raise HTTPException(status_code=404, detail="Source document not found.")

    circular_number = _field_value(doc, "circular_number") or doc.filename
    segment = _field_value(doc, "segment") or "unspecified"
    impact_area = _field_value(doc, "impact_area") or s.target
    summary = _field_value(doc, "summary") or ""
    effective_date = _field_value(doc, "effective_date")
    key_points = [f.field_value for f in doc.fields if f.field_name == "key_point" and f.field_value]

    title = f"[{circular_number}] {impact_area} change: {summary[:80]}" if summary else \
        f"[{circular_number}] {impact_area} change required"

    lines = [
        "## Source Circular",
        f"**Circular:** {circular_number}",
        f"**Segment:** {segment}",
        f"**Impact area:** {impact_area}",
        f"**Effective date:** {effective_date or '_Not specified in the notice — confirm before scheduling work._'}",
        "",
        "## Summary",
        summary or "_No summary extracted._",
        "",
    ]
    if key_points:
        lines.append("## Why this needs a change")
        lines.extend(f"- {kp}" for kp in key_points)
        lines.append("")

    if s.matched and s.candidates:
        lines.append("## Suggested code location(s)")
        for c in s.candidates:
            lines.append(f"- `{c['path']}` (lines {c['start_line']}–{c['end_line']}, similarity {c['score']:.2f})")
        lines.append("")
        lines.append("_Suggested by local repo search — verify against the current codebase before starting work._")
    else:
        lines.append("## Suggested code location(s)")
        lines.append(s.reason or "No strong match found — likely new functionality.")

    if s.reviewer_note:
        lines.append("")
        lines.append("## Reviewer notes")
        lines.append(s.reviewer_note)

    return JiraTicketDraft(title=title, description="\n".join(lines))


@router.post("/{suggestion_id}/jira-key", response_model=ChangeSuggestionDetailOut)
def save_jira_ticket_key(suggestion_id: str, body: JiraTicketKeySave, db: Session = Depends(get_db)):
    """Records the real JIRA ticket key after it's been created manually, for traceability
    back from this review to the actual tracked work."""
    s = db.get(ChangeSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Change suggestion not found.")
    s.jira_ticket_key = body.jira_ticket_key
    db.commit()
    db.refresh(s)
    return _to_detail(s)
