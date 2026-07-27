import io

from app.services.extraction import ExtractionResult, ExtractedFieldResult


def _low_confidence_result():
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.9,
        fields=[
            ExtractedFieldResult("segment", "mutual_fund", 0.92, "subject line"),
            ExtractedFieldResult("impact_area", "backend", 0.4, "ambiguous clause"),
        ],
        extraction_notes="ambiguous clause",
        raw={},
    )


def _upload(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _low_confidence_result())
    resp = client.post(
        "/documents",
        files={"file": ("circular.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    return resp.json()["id"]


def test_confirm_review_item_closes_document(client, sample_pdf_bytes, monkeypatch):
    doc_id = _upload(client, sample_pdf_bytes, monkeypatch)
    queue = client.get("/review/queue").json()
    review_id = queue[0]["id"]

    resp = client.post(f"/review/{review_id}/resolve", json={"action": "confirm"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["status"] == "complete"


def test_correct_review_item_updates_field_value_and_confidence(client, sample_pdf_bytes, monkeypatch):
    doc_id = _upload(client, sample_pdf_bytes, monkeypatch)
    queue = client.get("/review/queue").json()
    review_id = queue[0]["id"]
    original_ai_value = queue[0]["original_value"]
    assert original_ai_value == "backend"  # the model's original (wrong-ish) guess

    resp = client.post(
        f"/review/{review_id}/resolve",
        json={"action": "correct", "corrected_value": "frontend", "reviewer_note": "actually a UI-only change"},
    )
    assert resp.status_code == 200
    assert resp.json()["corrected_value"] == "frontend"
    # Regression: the original AI-predicted value must survive the correction - it's the
    # labeled pair (predicted vs. correct) needed to ever evaluate/retune confidence later.
    assert resp.json()["original_value"] == "backend"

    detail = client.get(f"/documents/{doc_id}").json()
    impact_field = next(f for f in detail["fields"] if f["field_name"] == "impact_area")
    assert impact_field["field_value"] == "frontend"
    assert impact_field["confidence"] == 1.0


def test_correct_without_value_is_rejected(client, sample_pdf_bytes, monkeypatch):
    doc_id = _upload(client, sample_pdf_bytes, monkeypatch)
    review_id = client.get("/review/queue").json()[0]["id"]

    resp = client.post(f"/review/{review_id}/resolve", json={"action": "correct"})
    assert resp.status_code == 400
