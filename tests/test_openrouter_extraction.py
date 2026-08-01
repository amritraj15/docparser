import json

import httpx
import pytest

from app.config import settings
from app.services.extraction import extract_document, ExtractionError


@pytest.fixture()
def openrouter_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-key")
    yield
    monkeypatch.setattr(settings, "llm_provider", "anthropic")


@pytest.fixture()
def real_pdf_path(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 minimal but non-empty content for base64 encoding\n%%EOF")
    return str(pdf_path)


def _fake_payload():
    return {
        "doc_type": "circular",
        "doc_type_confidence": 0.91,
        "segment": {"value": "mutual_fund", "confidence": 0.85, "source_note": "subject line"},
        "system_impacting": {"value": True, "confidence": 0.8, "source_note": "clause 2"},
    }


def _openai_style_response(payload_dict):
    return {
        "id": "gen-123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "record_circular_classification",
                                "arguments": json.dumps(payload_dict),
                            },
                        }
                    ],
                }
            }
        ],
    }


def test_extract_via_openrouter_success(monkeypatch, openrouter_provider, real_pdf_path):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return _openai_style_response(_fake_payload())

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)

    result = extract_document(real_pdf_path)

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test-key"
    assert captured["json"]["tool_choice"]["function"]["name"] == "record_circular_classification"
    # PDF must go in as a `file` content block with a base64 data URL, not Anthropic's
    # `document` block shape - this is the actual thing worth regression-testing here.
    user_content = captured["json"]["messages"][1]["content"]
    file_block = next(b for b in user_content if b["type"] == "file")
    assert file_block["file"]["file_data"].startswith("data:application/pdf;base64,")

    assert result.doc_type == "circular"
    by_name = {f.field_name: f for f in result.fields}
    assert by_name["segment"].value == "mutual_fund"


def test_extract_via_openrouter_sends_optional_headers_when_configured(monkeypatch, openrouter_provider, real_pdf_path):
    monkeypatch.setattr(settings, "openrouter_site_url", "https://example.com")
    monkeypatch.setattr(settings, "openrouter_app_name", "my-app")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return _openai_style_response(_fake_payload())

    def fake_post(url, json, headers, timeout):
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)
    extract_document(real_pdf_path)

    assert captured["headers"]["HTTP-Referer"] == "https://example.com"
    assert captured["headers"]["X-Title"] == "my-app"


def test_extract_via_openrouter_without_api_key(monkeypatch, openrouter_provider, real_pdf_path):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with pytest.raises(ExtractionError, match="OPENROUTER_API_KEY"):
        extract_document(real_pdf_path)


def test_extract_via_openrouter_connection_error(monkeypatch, openrouter_provider, real_pdf_path):
    def fake_post(url, json, headers, timeout):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)
    with pytest.raises(ExtractionError, match="Could not reach OpenRouter"):
        extract_document(real_pdf_path)


def test_extract_via_openrouter_surfaces_credit_error_body(monkeypatch, openrouter_provider, real_pdf_path):
    """Mirrors the real 'credit balance too low' style error this project has already hit
    with direct Anthropic - confirms the OpenRouter path surfaces the body, not just a
    generic status code, so the actual problem (auth vs. credits vs. bad model slug) is
    visible in Document.error_message rather than swallowed."""
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        402,
        json={"error": {"message": "Insufficient credits.", "type": "insufficient_quota"}},
        request=request,
    )

    def fake_post(url, json, headers, timeout):
        raise httpx.HTTPStatusError("insufficient credits", request=request, response=response)

    monkeypatch.setattr("app.services.extraction.httpx.post", fake_post)
    with pytest.raises(ExtractionError, match="Insufficient credits"):
        extract_document(real_pdf_path)


def test_extract_via_openrouter_no_tool_call_returned(monkeypatch, openrouter_provider, real_pdf_path):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "I can't do that."}}]}

    monkeypatch.setattr("app.services.extraction.httpx.post", lambda url, json, headers, timeout: FakeResponse())
    with pytest.raises(ExtractionError, match="did not return a tool call"):
        extract_document(real_pdf_path)


def test_extract_via_openrouter_malformed_tool_arguments(monkeypatch, openrouter_provider, real_pdf_path):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "function": {"name": "record_circular_classification", "arguments": "not valid json"}
                        }]
                    }
                }]
            }

    monkeypatch.setattr("app.services.extraction.httpx.post", lambda url, json, headers, timeout: FakeResponse())
    with pytest.raises(ExtractionError, match="not valid JSON"):
        extract_document(real_pdf_path)


def test_unknown_provider_error_message_lists_openrouter(monkeypatch, real_pdf_path):
    monkeypatch.setattr(settings, "llm_provider", "carrier_pigeon")
    with pytest.raises(ExtractionError, match="openrouter"):
        extract_document(real_pdf_path)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
