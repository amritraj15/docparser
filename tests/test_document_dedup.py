import io

from app.config import settings
from app.services.extraction import ExtractionResult, ExtractedFieldResult


CALL_COUNT = {"n": 0}


def _counting_result(segment="mutual_fund", impact_area="backend", confidence=0.9):
    CALL_COUNT["n"] += 1
    return ExtractionResult(
        doc_type="circular",
        doc_type_confidence=0.95,
        fields=[
            ExtractedFieldResult("segment", segment, confidence, "subject line"),
            ExtractedFieldResult("system_impacting", True, confidence, "clause 1"),
            ExtractedFieldResult("impact_area", impact_area, confidence, "clause 1"),
        ],
        extraction_notes=None,
        raw={"call": CALL_COUNT["n"]},
    )


def _upload(client, pdf_bytes, filename="circular.pdf"):
    return client.post(
        "/documents",
        files={"file": (filename, io.BytesIO(pdf_bytes), "application/pdf")},
    )


def setup_function():
    CALL_COUNT["n"] = 0


def test_second_upload_of_identical_pdf_reuses_result_without_a_new_llm_call(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())

    first = _upload(client, sample_pdf_bytes, "first.pdf")
    first_id = first.json()["id"]
    second = _upload(client, sample_pdf_bytes, "second.pdf")  # identical bytes, different filename
    second_id = second.json()["id"]

    assert CALL_COUNT["n"] == 1  # the LLM was only actually called once

    second_detail = client.get(f"/documents/{second_id}").json()
    assert second_detail["reused_from_document_id"] == first_id
    assert second_detail["status"] == "complete"

    # fields were copied, not left empty
    by_name = {f["field_name"]: f for f in second_detail["fields"]}
    assert by_name["segment"]["field_value"] == "mutual_fund"
    assert by_name["impact_area"]["field_value"] == "backend"


def test_different_pdf_bytes_does_not_reuse(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())

    _upload(client, sample_pdf_bytes, "first.pdf")
    different_bytes = sample_pdf_bytes + b"\n% a genuinely different file"
    second = _upload(client, different_bytes, "second.pdf")

    assert CALL_COUNT["n"] == 2  # two distinct files -> two real LLM calls
    assert client.get(f"/documents/{second.json()['id']}").json()["reused_from_document_id"] is None


def test_provider_change_invalidates_the_cache(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())

    _upload(client, sample_pdf_bytes, "first.pdf")
    monkeypatch.setattr(settings, "llm_provider", "ollama")  # switch provider
    second = _upload(client, sample_pdf_bytes, "second.pdf")

    assert CALL_COUNT["n"] == 2  # provider changed -> not eligible for reuse
    assert client.get(f"/documents/{second.json()['id']}").json()["reused_from_document_id"] is None


def test_prompt_schema_change_invalidates_the_cache(client, sample_pdf_bytes, monkeypatch):
    """Simulates a code change to the prompt/schema between two uploads - the contract
    fingerprint must catch this even though provider/model are unchanged."""
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())

    _upload(client, sample_pdf_bytes, "first.pdf")

    call_n = {"n": 0}

    def changing_fingerprint():
        call_n["n"] += 1
        return f"fingerprint-{call_n['n']}"  # different every call, simulating a prompt edit

    monkeypatch.setattr("app.services.pipeline.contract_fingerprint", changing_fingerprint)
    second = _upload(client, sample_pdf_bytes, "second.pdf")

    assert CALL_COUNT["n"] == 2
    assert client.get(f"/documents/{second.json()['id']}").json()["reused_from_document_id"] is None


def test_failed_prior_document_is_not_used_as_a_reuse_source(client, sample_pdf_bytes, monkeypatch):
    from app.services.extraction import ExtractionError

    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: (_ for _ in ()).throw(ExtractionError("boom")),
    )
    first = _upload(client, sample_pdf_bytes, "first.pdf")
    assert client.get(f"/documents/{first.json()['id']}").json()["status"] == "failed"

    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())
    second = _upload(client, sample_pdf_bytes, "second.pdf")

    assert CALL_COUNT["n"] == 1  # the failed first attempt must not be reused as a success
    assert client.get(f"/documents/{second.json()['id']}").json()["status"] == "complete"


def test_reprocess_always_bypasses_reuse_even_with_a_matching_cache(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr("app.services.pipeline.extract_document", lambda p: _counting_result())

    first = _upload(client, sample_pdf_bytes, "first.pdf")
    second = _upload(client, sample_pdf_bytes, "second.pdf")
    assert CALL_COUNT["n"] == 1  # second was a cache hit

    resp = client.post(f"/documents/{second.json()['id']}/reprocess")
    assert resp.status_code == 200
    assert CALL_COUNT["n"] == 2  # reprocess forces a real call regardless of the cache
    assert resp.json()["reused_from_document_id"] is None


def test_correcting_a_reused_documents_field_does_not_affect_the_source(client, sample_pdf_bytes, monkeypatch):
    """The whole point of copying original values instead of linking rows: each document's
    review lifecycle is independent."""
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: _counting_result(impact_area="backend", confidence=0.4),  # below threshold -> review
    )

    first = _upload(client, sample_pdf_bytes, "first.pdf").json()
    second = _upload(client, sample_pdf_bytes, "second.pdf").json()
    assert CALL_COUNT["n"] == 1

    queue = client.get("/review/queue").json()
    second_impact_area_review = next(
        r for r in queue if r["document_id"] == second["id"] and r["field_name"] == "impact_area"
    )
    assert second_impact_area_review["original_value"] == "backend"

    client.post(
        f"/review/{second_impact_area_review['id']}/resolve",
        json={"action": "correct", "corrected_value": "frontend"},
    )

    # the reused (second) document reflects the correction...
    second_detail = client.get(f"/documents/{second['id']}").json()
    fixed = next(f for f in second_detail["fields"] if f["field_name"] == "impact_area")
    assert fixed["field_value"] == "frontend"

    # ...but the ORIGINAL source document is untouched
    first_detail = client.get(f"/documents/{first['id']}").json()
    original = next(f for f in first_detail["fields"] if f["field_name"] == "impact_area")
    assert original["field_value"] == "backend"


def test_reused_low_confidence_fields_get_their_own_review_items(client, sample_pdf_bytes, monkeypatch):
    monkeypatch.setattr(
        "app.services.pipeline.extract_document",
        lambda p: _counting_result(confidence=0.3),
    )

    first = _upload(client, sample_pdf_bytes, "first.pdf").json()
    second = _upload(client, sample_pdf_bytes, "second.pdf").json()

    queue = client.get("/review/queue").json()
    first_ids = {r["id"] for r in queue if r["document_id"] == first["id"]}
    second_ids = {r["id"] for r in queue if r["document_id"] == second["id"]}

    assert len(first_ids) > 0
    assert len(second_ids) > 0
    assert first_ids.isdisjoint(second_ids)  # fully independent review items, not shared
