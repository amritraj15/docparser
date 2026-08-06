import io

from app.config import settings
from app.services import repo_index as ri
from app.services.extraction import ExtractionResult, ExtractedFieldResult


def _write(root, rel_path, content):
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return str(full)


KEYWORDS = ["scheme_code"]


def _fake_embed(texts):
    return [[1.0 if kw in t.lower() else 0.0 for kw in KEYWORDS] for t in texts]


def _impacting_result():
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.95,
        fields=[
            ExtractedFieldResult("circular_number", "20260722-30", 0.95, "header"),
            ExtractedFieldResult("segment", "mutual_fund", 0.9, "subject"),
            ExtractedFieldResult("system_impacting", True, 0.9, "clause 1"),
            ExtractedFieldResult("impact_area", "backend", 0.9, "clause 1"),
            ExtractedFieldResult("summary", "New scheme_code field required in order file.", 0.9, "body"),
            ExtractedFieldResult("effective_date", "2026-08-01", 0.9, "top"),
            ExtractedFieldResult("key_point", "New mandatory scheme_code field", 0.9, "clause 1",
                                  is_list_item=True, list_item_index=0),
        ],
        extraction_notes=None,
        raw={},
    )


def _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path):
    repo_root = tmp_path / "backend_repo"
    _write(repo_root, "app/order_schema.py", "SCHEME_CODE_FIELD = 'scheme_code'\n")

    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr(settings, "backend_repo_path", str(repo_root))
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr(ri, "_embed_texts", _fake_embed)
    client.post("/repo-index/build", params={"target": "backend"})

    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _impacting_result())
    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]

    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    suggestion = resp.json()[0]
    return doc_id, suggestion


def test_suggestion_appears_in_dashboard_listing(client, sample_pdf_bytes, monkeypatch, tmp_path):
    _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)

    resp = client.get("/change-suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    # denormalized document context, so the dashboard doesn't need a second fetch per row
    assert body[0]["circular_number"] == "20260722-30"
    assert body[0]["segment"] == "mutual_fund"
    assert body[0]["status"] == "pending"


def test_dashboard_matched_only_filter_excludes_no_match_results(client, sample_pdf_bytes, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _impacting_result())
    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]
    client.post(f"/documents/{doc_id}/suggest-changes")  # no index built -> unmatched

    assert client.get("/change-suggestions").json() == []  # matched_only=True default
    unmatched = client.get("/change-suggestions", params={"matched_only": False}).json()
    assert len(unmatched) == 1
    assert unmatched[0]["matched"] is False


def test_review_approve_records_reviewer(client, sample_pdf_bytes, monkeypatch, tmp_path):
    _, suggestion = _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)

    resp = client.post(
        f"/change-suggestions/{suggestion['id']}/review",
        json={"status": "approved", "reviewer_name": "Priya (PM)", "reviewer_note": "Confirmed with lead, ship it"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reviewer_name"] == "Priya (PM)"
    assert body["reviewer_note"] == "Confirmed with lead, ship it"


def test_review_rejects_invalid_status(client, sample_pdf_bytes, monkeypatch, tmp_path):
    _, suggestion = _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)
    resp = client.post(f"/change-suggestions/{suggestion['id']}/review", json={"status": "maybe_later"})
    assert resp.status_code == 400


def test_rerunning_suggest_changes_does_not_overwrite_a_recorded_review(client, sample_pdf_bytes, monkeypatch, tmp_path):
    """The core reason this got persisted at all: a PM's decision must survive re-running
    the search (e.g. after a re-index), not get silently clobbered."""
    doc_id, suggestion = _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)

    client.post(
        f"/change-suggestions/{suggestion['id']}/review",
        json={"status": "needs_discussion", "reviewer_name": "Amrit", "reviewer_note": "Check with backend lead first"},
    )

    # Re-run suggest-changes for the same document - must NOT reset the review.
    resp = client.post(f"/documents/{doc_id}/suggest-changes")
    refreshed = resp.json()[0]
    assert refreshed["id"] == suggestion["id"]
    assert refreshed["status"] == "needs_discussion"
    assert refreshed["reviewer_name"] == "Amrit"


def test_jira_ticket_draft_includes_circular_context_and_candidates(client, sample_pdf_bytes, monkeypatch, tmp_path):
    _, suggestion = _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)

    resp = client.get(f"/change-suggestions/{suggestion['id']}/jira-ticket")
    assert resp.status_code == 200
    draft = resp.json()
    assert "20260722-30" in draft["title"]
    assert "backend" in draft["title"]
    assert "app/order_schema.py" in draft["description"]
    assert "scheme_code" in draft["description"]
    assert "2026-08-01" in draft["description"]  # effective_date


def test_jira_ticket_draft_for_unmatched_suggestion_says_so(client, sample_pdf_bytes, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "repo_suggestion_enabled", True)
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _impacting_result())
    resp = client.post("/documents", files={"file": ("c.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")})
    doc_id = resp.json()["id"]
    suggestion = client.post(f"/documents/{doc_id}/suggest-changes").json()[0]

    resp = client.get(f"/change-suggestions/{suggestion['id']}/jira-ticket")
    assert "No index built" in resp.json()["description"] or "No strong match" in resp.json()["description"]


def test_save_jira_ticket_key_for_traceability(client, sample_pdf_bytes, monkeypatch, tmp_path):
    _, suggestion = _setup_matched_suggestion(client, sample_pdf_bytes, monkeypatch, tmp_path)

    resp = client.post(f"/change-suggestions/{suggestion['id']}/jira-key", json={"jira_ticket_key": "KUV-1234"})
    assert resp.status_code == 200
    assert resp.json()["jira_ticket_key"] == "KUV-1234"

    # persisted - shows up on a fresh fetch, not just the response of the write itself
    detail = client.get(f"/change-suggestions/{suggestion['id']}").json()
    assert detail["jira_ticket_key"] == "KUV-1234"


def test_get_nonexistent_suggestion_404s(client):
    resp = client.get("/change-suggestions/does-not-exist")
    assert resp.status_code == 404
