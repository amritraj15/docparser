import io

from app.services.extraction import ExtractionResult, ExtractedFieldResult


def _result(segment, system_impacting, impact_area, effective_date, subject):
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.95,
        fields=[
            ExtractedFieldResult("segment", segment, 0.95, "subject line"),
            ExtractedFieldResult("system_impacting", system_impacting, 0.95, "clause 1"),
            ExtractedFieldResult("impact_area", impact_area, 0.95, "clause 1"),
            ExtractedFieldResult("effective_date", effective_date, 0.95, "top"),
            ExtractedFieldResult("subject", subject, 0.95, "header"),
        ],
        extraction_notes=None,
        raw={},
    )


def _upload(client, sample_pdf_bytes, monkeypatch, segment, system_impacting, impact_area, effective_date, subject):
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: _result(segment, system_impacting, impact_area, effective_date, subject),
    )
    resp = client.post(
        "/documents",
        files={"file": (f"{subject}.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    return resp.json()["id"]


def test_query_filters_by_segment_and_impact_area(client, sample_pdf_bytes, monkeypatch):
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "backend", "2026-01-01", "New order file field")
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "frontend", "2026-02-01", "New disclosure screen")
    _upload(client, sample_pdf_bytes, monkeypatch, "equity", True, "backend", "2026-01-15", "Equity margin change")

    resp = client.get("/query/documents", params={"segment": "mutual_fund", "impact_area": "backend"})
    results = resp.json()
    assert len(results) == 1
    assert any(f["field_value"] == "New order file field" for f in results[0]["fields"])


def test_query_filters_by_system_impacting(client, sample_pdf_bytes, monkeypatch):
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "backend", "2026-01-01", "System change notice")
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", False, "none", "2026-01-01", "Trading holiday")

    resp = client.get("/query/documents", params={"system_impacting": "true"})
    results = resp.json()
    assert len(results) == 1
    assert any(f["field_value"] == "System change notice" for f in results[0]["fields"])


def test_query_filters_by_date_range(client, sample_pdf_bytes, monkeypatch):
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "backend", "2026-01-01", "Early change")
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "backend", "2026-06-15", "Late change")

    resp = client.get("/query/documents", params={"date_from": "2026-05-01"})
    results = resp.json()
    assert len(results) == 1
    assert any(f["field_value"] == "Late change" for f in results[0]["fields"])


def test_query_freeform_text_search(client, sample_pdf_bytes, monkeypatch):
    _upload(client, sample_pdf_bytes, monkeypatch, "mutual_fund", True, "backend", "2026-01-01", "New scheme code format")
    _upload(client, sample_pdf_bytes, monkeypatch, "equity", True, "backend", "2026-01-01", "Margin change")

    resp = client.get("/query/documents", params={"text": "scheme code"})
    results = resp.json()
    assert len(results) == 1
