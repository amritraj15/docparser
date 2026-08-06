import pytest

from app.services.extraction import _normalize, extract_document, ExtractionError


def test_normalize_scalar_and_key_point_fields():
    payload = {
        "doc_type": "circular",
        "doc_type_confidence": 0.97,
        "circular_number": {"value": "20260722-30", "confidence": 0.95, "source_note": "header"},
        "segment": {"value": "mutual_fund", "confidence": 0.9, "source_note": "subject line"},
        "system_impacting": {"value": True, "confidence": 0.85, "source_note": "clause 3"},
        "impact_area": {"value": "backend", "confidence": 0.8, "source_note": "clause 3"},
        "effective_date": {"value": None, "confidence": 0.2, "source_note": None},
        "key_points": [
            {"point": {"value": "New mandatory field 'scheme_code_v2' in order file", "confidence": 0.75, "source_note": "clause 3, page 2"}},
        ],
        "extraction_notes": "Annexure referenced but not attached.",
    }

    result = _normalize(payload)

    assert result.doc_type == "circular"
    assert result.doc_type_confidence == 0.97
    assert result.extraction_notes == "Annexure referenced but not attached."

    by_name = {f.field_name: f for f in result.fields if not f.is_list_item}
    assert by_name["circular_number"].value == "20260722-30"
    assert by_name["segment"].value == "mutual_fund"
    assert by_name["system_impacting"].value is True
    assert by_name["impact_area"].value == "backend"
    # A field with value=None is still recorded (not silently dropped), just low confidence
    assert by_name["effective_date"].value is None
    assert by_name["effective_date"].confidence == 0.2

    key_points = [f for f in result.fields if f.is_list_item]
    assert len(key_points) == 1
    assert key_points[0].field_name == "key_point"
    assert "scheme_code_v2" in key_points[0].value
    assert key_points[0].list_item_index == 0


def test_normalize_informational_circular_has_no_key_points():
    # A purely informational circular (holiday notice, FYI) should classify as not
    # system_impacting and typically carries no key_points at all.
    payload = {
        "doc_type": "circular",
        "doc_type_confidence": 0.95,
        "subject": {"value": "Trading holiday - Republic Day", "confidence": 0.98, "source_note": "header"},
        "system_impacting": {"value": False, "confidence": 0.95, "source_note": "whole document"},
        "impact_area": {"value": "none", "confidence": 0.9, "source_note": "whole document"},
    }

    result = _normalize(payload)

    by_name = {f.field_name: f for f in result.fields}
    assert by_name["system_impacting"].value is False
    assert by_name["impact_area"].value == "none"
    assert not any(f.is_list_item for f in result.fields)


def test_normalize_missing_scalar_fields_become_low_confidence_rows_not_silent_gaps():
    # A model that omits fields entirely (not "null with low confidence" - genuinely
    # absent) must not have those fields silently vanish. A missing field is otherwise
    # indistinguishable from a working classification and never reaches the review queue -
    # see decisions.md #24, prompted by a real gemma4-via-Ollama response that did exactly
    # this for every field except system_impacting.
    payload = {"doc_type": "unknown", "doc_type_confidence": 0.1}

    result = _normalize(payload)

    assert result.doc_type == "unknown"
    by_name = {f.field_name: f for f in result.fields}
    for name in ["circular_number", "circular_date", "subject", "segment",
                 "system_impacting", "impact_area", "effective_date", "summary"]:
        assert by_name[name].value is None
        assert by_name[name].confidence == 0.0
        assert "not present" in by_name[name].source_note.lower()


def test_normalize_partial_response_flags_only_the_missing_fields():
    # Regression test for the actual incident: a model returned system_impacting
    # correctly but omitted every other scalar field, and stuffed self-invented
    # dict-like text into key_points instead of plain English. The present field must be
    # trusted as given; only the missing ones should be flagged low-confidence.
    payload = {
        "doc_type": "circular",
        "doc_type_confidence": 0.98,
        "system_impacting": {"value": True, "confidence": 1.0, "source_note": None},
        "key_points": [
            {"point": {"value": "{'field': 'System Logic Change', 'description': '...'}", "confidence": 1.0}},
        ],
    }

    result = _normalize(payload)
    by_name = {f.field_name: f for f in result.fields if not f.is_list_item}

    assert by_name["system_impacting"].value is True
    assert by_name["system_impacting"].confidence == 1.0  # present field: trust it as given
    assert by_name["impact_area"].value is None
    assert by_name["impact_area"].confidence == 0.0        # missing field: flagged, not silent
    assert by_name["segment"].confidence == 0.0


def test_extract_document_raises_on_missing_file():
    with pytest.raises(ExtractionError):
        extract_document("/nonexistent/path/does-not-exist.pdf")


def test_extract_document_raises_on_empty_file(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")

    with pytest.raises(ExtractionError):
        extract_document(str(empty))


def test_extract_document_raises_without_api_key(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")

    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        extract_document(str(pdf))


def test_malformed_field_shape_raises_extraction_error_not_unhandled_exception(monkeypatch):
    """
    Regression test: a model returning a bare string instead of a {value, confidence,
    source_note} object for a field must surface as ExtractionError (catchable, marks the
    document FAILED/retryable) — not an unhandled AttributeError that would leave the
    document stuck in PROCESSING forever. Found via plan-review's Software Engineer lens.
    """
    from app.services.extraction import _safe_normalize

    malformed_payload = {
        "doc_type": "circular",
        "doc_type_confidence": 0.9,
        "segment": "mutual_fund",  # malformed: should be {"value": ..., "confidence": ...}
    }

    with pytest.raises(ExtractionError, match="malformed structured output"):
        _safe_normalize(malformed_payload)
