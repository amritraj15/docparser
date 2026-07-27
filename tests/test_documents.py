import io

from app.services.extraction import ExtractionResult, ExtractedFieldResult, ExtractionError


def _fake_impacting_result():
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.96,
        fields=[
            ExtractedFieldResult("circular_number", "20260722-30", 0.94, "header"),
            ExtractedFieldResult("segment", "mutual_fund", 0.92, "subject line"),
            ExtractedFieldResult("system_impacting", True, 0.9, "clause 3"),
            ExtractedFieldResult("impact_area", "backend", 0.88, "clause 3"),
        ],
        extraction_notes=None,
        raw={"doc_type": "circular"},
    )


def _fake_low_confidence_result():
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.9,
        fields=[
            ExtractedFieldResult("segment", "mutual_fund", 0.9, "subject line"),
            # This field should be routed to the review queue.
            ExtractedFieldResult("impact_area", "backend", 0.4, "ambiguous clause 5"),
        ],
        extraction_notes="Clause 5 references an annexure that wasn't attached.",
        raw={"doc_type": "circular"},
    )


def test_upload_rejects_non_pdf(client):
    resp = client.post(
        "/documents",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert resp.status_code == 415


def test_upload_rejects_empty_file(client, sample_pdf_bytes):
    resp = client.post(
        "/documents",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert resp.status_code == 400


def test_upload_and_process_high_confidence(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda file_path: _fake_impacting_result(),
    )

    resp = client.post(
        "/documents",
        files={"file": ("circular.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "complete"
    assert detail["doc_type"] == "circular"
    field_names = {f["field_name"] for f in detail["fields"]}
    assert {"circular_number", "segment", "system_impacting", "impact_area"} <= field_names


def test_upload_and_process_low_confidence_creates_review_item(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda file_path: _fake_low_confidence_result(),
    )

    resp = client.post(
        "/documents",
        files={"file": ("circular.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    doc_id = resp.json()["id"]

    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "needs_review"

    queue = client.get("/review/queue").json()
    assert len(queue) == 1
    assert queue[0]["document_id"] == doc_id
    assert queue[0]["field_name"] == "impact_area"


def test_extraction_failure_marks_document_failed(client, sample_pdf_bytes, monkeypatch):
    def _raise(file_path):
        raise ExtractionError("model timed out")

    monkeypatch.setattr("app.services.pipeline.extract_document", _raise)

    resp = client.post(
        "/documents",
        files={"file": ("circular.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    doc_id = resp.json()["id"]

    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "failed"
    assert "model timed out" in detail["error_message"]


def test_reprocess_after_failure_can_succeed(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda file_path: (_ for _ in ()).throw(ExtractionError("transient error")),
    )
    resp = client.post(
        "/documents",
        files={"file": ("circular.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    doc_id = resp.json()["id"]
    assert client.get(f"/documents/{doc_id}").json()["status"] == "failed"

    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda file_path: _fake_impacting_result(),
    )
    resp2 = client.post(f"/documents/{doc_id}/reprocess")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "complete"
