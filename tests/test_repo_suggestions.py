import io

from app.config import settings
from app.services import repo_index as ri
from app.services.extraction import ExtractionResult, ExtractedFieldResult


def _write(root, rel_path, content):
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return str(full)


KEYWORDS = ["scheme_code", "holiday_calendar"]


def _fake_embed(texts):
    vectors = []
    for t in texts:
        lower = t.lower()
        vectors.append([1.0 if kw in lower else 0.0 for kw in KEYWORDS])
    return vectors


def _impacting_result(key_point_text):
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.95,
        fields=[
            ExtractedFieldResult("segment", "mutual_fund", 0.9, "subject"),
            ExtractedFieldResult("system_impacting", True, 0.9, "clause 1"),
            ExtractedFieldResult("impact_area", "backend", 0.9, "clause 1"),
            ExtractedFieldResult("summary", key_point_text, 0.9, "body"),
            ExtractedFieldResult("key_point", key_point_text, 0.9, "clause 1", is_list_item=True, list_item_index=0),
        ],
        extraction_notes=None,
        raw={},
    )


def _non_impacting_result():
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.95,
        fields=[
            ExtractedFieldResult("segment", "mutual_fund", 0.9, "subject"),
            ExtractedFieldResult("system_impacting", False, 0.9, "whole doc"),
            ExtractedFieldResult("impact_area", "none", 0.9, "whole doc"),
        ],
        extraction_notes=None,
        raw={},
    )


def test_repo_index_build_disabled_by_default(client):
    resp = client.post("/repo-index/build", params={"target": "backend"})
    assert resp.status_code == 403


def test_suggest_changes_disabled_by_default(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _impacting_result("new scheme_code field"))
    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]

    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    assert resp.status_code == 403


def test_suggest_changes_requires_system_impacting(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _non_impacting_result())

    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]

    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    assert resp.status_code == 400
    assert "system_impacting" in resp.json()["detail"]


def test_suggest_changes_end_to_end(client, sample_pdf_bytes, monkeypatch, tmp_path):
    repo_root = tmp_path / "backend_repo"
    _write(repo_root, "app/order_schema.py", "SCHEME_CODE_FIELD = 'scheme_code'\n")
    _write(repo_root, "app/holidays.py", "HOLIDAY_CALENDAR = ['2026-01-26']\n")

    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr(settings, "backend_repo_path", str(repo_root))
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr(ri, "_embed_texts", _fake_embed)

    build_resp = client.post("/repo-index/build", params={"target": "backend"})
    assert build_resp.status_code == 200
    assert build_resp.json()["files_scanned"] == 2

    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: _impacting_result("New mandatory scheme_code field in order file"),
    )
    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]

    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"]["backend"]["matched"] is True
    assert body["results"]["backend"]["candidates"][0]["path"] == "app/order_schema.py"


def test_suggest_changes_no_index_built_reports_reason_not_500(client, sample_pdf_bytes, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: _impacting_result("new scheme_code field"),
    )

    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]

    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    assert resp.status_code == 200
    assert resp.json()["results"]["backend"]["matched"] is False
    assert "No index built" in resp.json()["results"]["backend"]["reason"]
