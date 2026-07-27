import json

import httpx
import pytest

from app.config import settings
from app.services.extraction import extract_document, ExtractionError


@pytest.fixture()
def ollama_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    yield
    monkeypatch.setattr(settings, "llm_provider", "anthropic")


@pytest.fixture()
def real_pdf_bytes():
    # A tiny single-page PDF that PyMuPDF can actually open and rasterize.
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources<<>>/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 0>>stream\n\nendstream endobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"trailer<</Size 5/Root 1 0 R>>\n"
        b"startxref\n0\n%%EOF"
    )


def _fake_payload():
    return {
        "doc_type": "circular",
        "doc_type_confidence": 0.88,
        "segment": {"value": "mutual_fund", "confidence": 0.8, "source_note": "subject line"},
        "system_impacting": {"value": True, "confidence": 0.7, "source_note": "clause 2"},
    }


def test_extract_via_ollama_success(monkeypatch, ollama_provider, real_pdf_bytes, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(real_pdf_bytes)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": json.dumps(_fake_payload())}}

    def fake_post(url, json, timeout):
        assert url.endswith("/api/chat")
        assert json["model"] == settings.ollama_model
        assert "images" in json["messages"][1]
        return FakeResponse()

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)

    result = extract_document(str(pdf_path))
    assert result.doc_type == "circular"
    by_name = {f.field_name: f for f in result.fields}
    assert by_name["segment"].value == "mutual_fund"


def test_extract_via_ollama_connection_error(monkeypatch, ollama_provider, real_pdf_bytes, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(real_pdf_bytes)

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)

    with pytest.raises(ExtractionError, match="ollama serve"):
        extract_document(str(pdf_path))


def test_extract_via_ollama_invalid_json(monkeypatch, ollama_provider, real_pdf_bytes, tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(real_pdf_bytes)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "not valid json"}}

    monkeypatch.setattr("app.services.extraction.httpx.post", lambda url, json, timeout: FakeResponse())

    with pytest.raises(ExtractionError, match="valid JSON"):
        extract_document(str(pdf_path))


def test_extract_via_ollama_malformed_pdf(monkeypatch, ollama_provider, tmp_path):
    pdf_path = tmp_path / "bad.pdf"
    pdf_path.write_bytes(b"this is not a pdf at all")

    with pytest.raises(ExtractionError):
        extract_document(str(pdf_path))


def test_unknown_provider_raises(monkeypatch, real_pdf_bytes, tmp_path):
    monkeypatch.setattr(settings, "llm_provider", "carrier_pigeon")
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(real_pdf_bytes)

    with pytest.raises(ExtractionError, match="Unknown llm_provider"):
        extract_document(str(pdf_path))

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
