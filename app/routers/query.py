from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Document
from app.schemas import DocumentDetailOut

router = APIRouter(prefix="/query", tags=["query"])


def _field_value(doc: Document, name: str) -> Optional[str]:
    for f in doc.fields:
        if f.field_name == name and not f.is_list_item:
            return f.field_value
    return None


@router.get("/documents", response_model=List[DocumentDetailOut])
def query_documents(
    doc_type: Optional[str] = None,
    segment: Optional[str] = Query(None, description="e.g. mutual_fund, equity, debt"),
    system_impacting: Optional[bool] = Query(None, description="Filter to circulars that require a system change"),
    impact_area: Optional[str] = Query(None, description="backend | frontend | both | none"),
    date_from: Optional[str] = Query(None, description="ISO date, inclusive lower bound on effective_date"),
    date_to: Optional[str] = Query(None, description="ISO date, inclusive upper bound on effective_date"),
    text: Optional[str] = Query(None, description="Substring match across any extracted/classified field value"),
    db: Session = Depends(get_db),
):
    """
    Filtering happens in Python over the fields already loaded per document rather than in
    SQL, because field values live in a flexible EAV-style table (see decisions.md). At demo
    scale this is simpler and just as fast; at real scale the hot filter fields (segment,
    system_impacting, effective_date) would get promoted to indexed columns on Document itself.
    """
    q = db.query(Document).options(joinedload(Document.fields))
    if doc_type:
        q = q.filter(Document.doc_type == doc_type)

    docs = q.all()
    results = []

    for doc in docs:
        if segment:
            v = _field_value(doc, "segment")
            if not v or v.lower() != segment.lower():
                continue

        if system_impacting is not None:
            v = _field_value(doc, "system_impacting")
            doc_impacting = str(v).strip().lower() in ("true", "1", "yes")
            if doc_impacting != system_impacting:
                continue

        if impact_area:
            v = _field_value(doc, "impact_area")
            if not v or v.lower() != impact_area.lower():
                continue

        if date_from or date_to:
            doc_date = _field_value(doc, "effective_date")
            if not doc_date:
                continue
            if date_from and doc_date < date_from:
                continue
            if date_to and doc_date > date_to:
                continue

        if text:
            haystack = " ".join((f.field_value or "") for f in doc.fields)
            if text.lower() not in haystack.lower():
                continue

        results.append(doc)

    return results
