"""
Document -> structured data, via an LLM. Two providers are supported behind one interface:

- Anthropic (Claude): PDF sent directly via native PDF understanding (base64 "document"
  content block), structured output forced via tool_choice on a single tool.
- Ollama (local): PDF rendered to page images (PyMuPDF), sent to a local vision model,
  structured output requested via Ollama's JSON-schema `format` parameter.

Both providers are given the *same* JSON schema (EXTRACTION_SCHEMA below) and produce the
same payload shape, which is why there's a single `_normalize()` shared by both. See
decisions.md for why Ollama support was added as an option rather than the default.

Design notes (see decisions.md for the full reasoning):
- Every field comes back with its own confidence + source_note. This is the actual hard
  part of the assignment: we do not treat extraction as all-or-nothing. A single low-
  confidence field routes that field (not the whole document) to human review.
- Structured output is schema-forced on both providers rather than "reply with JSON" in
  prose, which removes an entire class of "the model added a sentence before the JSON"
  parsing failures.
"""
import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
import httpx

from app.config import settings


class ExtractionError(Exception):
    """Raised when the document can't be read or the model fails to return usable structure."""


# The tool schema doubles as our data contract: every leaf value must be accompanied by a
# confidence score and a pointer back to where in the document it came from.
_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_note": {"type": "string"},
    },
    "required": ["confidence"],
}

# Segments Kuvera actually deals with on BSE. Kept as a single source of truth so the
# extraction schema and the /reference/segments dropdown endpoint can't drift apart.
SEGMENT_OPTIONS = [
    "mutual_fund", "equity", "debt", "derivatives", "currency", "commodity", "other",
]

IMPACT_AREA_OPTIONS = ["backend", "frontend", "both", "none"]


def _enum_field_schema(enum_values: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": "string", "enum": enum_values},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "source_note": {"type": "string"},
        },
        "required": ["confidence"],
    }


_BOOL_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_note": {"type": "string"},
    },
    "required": ["confidence"],
}

EXTRACTION_TOOL = {
    "name": "record_circular_classification",
    "description": (
        "Record the structured classification of a BSE notice/circular for a mutual-fund "
        "platform engineering team, including a per-field confidence score and a short note "
        "on where in the document that judgment came from. The goal is to tell an engineer, "
        "at a glance, whether this circular requires any system change and where to look — "
        "not just to summarize the document."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "description": "Best guess at document type, e.g. circular, notice, holiday_notice, unknown",
            },
            "doc_type_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "circular_number": _FIELD_SCHEMA,
            "circular_date": _FIELD_SCHEMA,
            "subject": _FIELD_SCHEMA,
            "segment": _enum_field_schema(SEGMENT_OPTIONS),
            "system_impacting": {
                **_BOOL_FIELD_SCHEMA,
                "description": (
                    "True if this circular requires ANY change to how a trading/transacting "
                    "platform's systems behave (new field, new file format, changed cutoff "
                    "time, new validation rule, API change, etc). False for purely informational "
                    "notices (holiday calendars, FYI announcements, personnel notices)."
                ),
            },
            "impact_area": _enum_field_schema(IMPACT_AREA_OPTIONS),
            "effective_date": _FIELD_SCHEMA,
            "summary": {
                **_FIELD_SCHEMA,
                "description": "2-3 sentence plain-language summary of what the circular actually says.",
            },
            "key_points": {
                "type": "array",
                "description": (
                    "The specific clauses that justify the system_impacting/impact_area "
                    "judgment — e.g. 'new mandatory field X in order file format', 'cutoff time "
                    "changed from 3pm to 1pm'. Each with its own confidence/source_note. Empty "
                    "for purely informational circulars."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "point": _FIELD_SCHEMA,
                    },
                },
            },
            "extraction_notes": {
                "type": "string",
                "description": (
                    "Anything anomalous: poor scan quality, missing pages, an annexure that "
                    "wasn't included, ambiguity about whether this applies to MF vs other segments."
                ),
            },
        },
        "required": ["doc_type", "doc_type_confidence"],
    },
}

# Same schema, exposed under a provider-neutral name — Ollama's structured-output `format`
# parameter takes a plain JSON schema, not an Anthropic tool wrapper.
EXTRACTION_SCHEMA = EXTRACTION_TOOL["input_schema"]

SYSTEM_PROMPT = (
    "You are a classification engine for BSE (Bombay Stock Exchange) notices and circulars, "
    "reading on behalf of a backend engineering team at a mutual-fund transacting platform "
    "integrated with BSE StAR MF. Your job is to tell the engineer, as precisely as possible: "
    "(1) does this circular require any change to the platform's systems, (2) is that change "
    "on the backend, frontend, or both, and (3) which segment does it apply to. Most circulars "
    "are purely informational (holiday calendars, personnel changes, FYI notices) and should be "
    "marked system_impacting=false with high confidence — do not over-flag. When a circular does "
    "describe a concrete operational change (new field, new file/message format, a changed "
    "cutoff time, a new validation rule, a new API or process step), mark it system_impacting=true "
    "and extract the specific clauses into key_points — each key_point's value must be a plain "
    "English sentence describing the clause, never a JSON object, dict, or code-like structure — "
    "so an engineer can jump straight to what matters instead of re-reading the whole PDF. Never "
    "guess a value you are not reasonably confident about without lowering its confidence score "
    "accordingly — a low-confidence correct-shaped answer is far more useful downstream than a "
    "confident wrong one. Every field in the schema must be present in your response, even when "
    "you are unsure — set value to null and confidence low rather than omitting the field "
    "entirely; an omitted field is indistinguishable from a bug and cannot be reviewed."
)


@dataclass
class ExtractedFieldResult:
    field_name: str
    value: Any
    confidence: float
    source_note: Optional[str] = None
    is_list_item: bool = False
    list_item_index: Optional[int] = None


@dataclass
class ExtractionResult:
    doc_type: str
    doc_type_confidence: float
    fields: list = field(default_factory=list)  # list[ExtractedFieldResult]
    extraction_notes: Optional[str] = None
    raw: Optional[dict] = None


def _anthropic_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _read_file_bytes(file_path: str) -> bytes:
    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise ExtractionError(f"Could not read uploaded file: {e}") from e

    if not data:
        raise ExtractionError("Uploaded file is empty.")

    return data


def extract_document(file_path: str, client: Optional[anthropic.Anthropic] = None) -> ExtractionResult:
    """
    Runs classification + field extraction and returns a normalized ExtractionResult.
    Dispatches to Anthropic, a local Ollama model, or OpenRouter based on
    settings.llm_provider. Raises ExtractionError on anything that prevents us from
    getting structured data back (unreadable file, API/connection failure, model
    declined/failed to return valid JSON).
    """
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        payload = _extract_via_anthropic(file_path, client=client)
    elif provider == "ollama":
        payload = _extract_via_ollama(file_path)
    elif provider == "openrouter":
        payload = _extract_via_openrouter(file_path)
    else:
        raise ExtractionError(
            f"Unknown llm_provider '{settings.llm_provider}' (expected 'anthropic', 'ollama', or 'openrouter')."
        )

    return _safe_normalize(payload)


def _safe_normalize(payload: dict) -> ExtractionResult:
    """
    _normalize() assumes each field is a {value, confidence, source_note} dict. A model
    can return syntactically valid JSON/tool-input that doesn't match that shape (e.g. a
    bare string instead of an object) — without this guard, that raises an unhandled
    AttributeError/TypeError that pipeline.process_document's `except ExtractionError`
    doesn't catch, leaving the document stuck in PROCESSING indefinitely instead of
    landing in FAILED (and retryable via /reprocess). Found via plan-review's Software
    Engineer lens — see decisions.md.
    """
    try:
        return _normalize(payload)
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        raise ExtractionError(f"Model returned malformed structured output: {e}") from e


def _extract_via_anthropic(file_path: str, client: Optional[anthropic.Anthropic] = None) -> dict:
    data = _read_file_bytes(file_path)
    b64 = base64.standard_b64encode(data).decode("utf-8")
    client = client or _anthropic_client()

    try:
        response = client.messages.create(
            model=settings.extraction_model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_circular_classification"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract this document's structured data per the tool schema.",
                        },
                    ],
                }
            ],
        )
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"LLM API error ({e.status_code}): {e.message}") from e
    except anthropic.APIError as e:
        raise ExtractionError(f"LLM API error: {e}") from e

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise ExtractionError("Model did not return structured extraction output.")

    return tool_use.input


def _pdf_to_page_images_b64(file_path: str, max_pages: int) -> list[str]:
    """Renders each PDF page (up to max_pages) to a base64-encoded PNG for a vision model."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ExtractionError(
            "PyMuPDF is required for the Ollama provider (pip install pymupdf)."
        ) from e

    data = _read_file_bytes(file_path)

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # PyMuPDF raises its own exception types for malformed PDFs
        raise ExtractionError(f"Could not open PDF: {e}") from e

    if pdf.page_count == 0:
        raise ExtractionError("PDF has no pages.")

    images_b64 = []
    # 200 DPI is a reasonable tradeoff for a local vision model: enough to read small
    # circular text without producing an image so large it blows the model's context/latency.
    zoom = 200 / 72
    matrix = fitz.Matrix(zoom, zoom)
    for page_index in range(min(pdf.page_count, max_pages)):
        pix = pdf[page_index].get_pixmap(matrix=matrix)
        images_b64.append(base64.standard_b64encode(pix.tobytes("png")).decode("utf-8"))

    return images_b64


def _extract_via_ollama(file_path: str) -> dict:
    images_b64 = _pdf_to_page_images_b64(file_path, max_pages=settings.ollama_max_pages)

    request_body = {
        "model": settings.ollama_model,
        "stream": False,
        "format": EXTRACTION_SCHEMA,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract this document's structured data as JSON matching the given "
                    "schema. Images are the document's pages, in order."
                ),
                "images": images_b64,
            },
        ],
    }

    try:
        resp = httpx.post(
            f"{settings.ollama_host.rstrip('/')}/api/chat",
            json=request_body,
            timeout=settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
    except httpx.ConnectError as e:
        raise ExtractionError(
            f"Could not reach Ollama at {settings.ollama_host} — is `ollama serve` running?"
        ) from e
    except httpx.HTTPStatusError as e:
        raise ExtractionError(f"Ollama API error ({e.response.status_code}): {e.response.text}") from e
    except httpx.HTTPError as e:
        raise ExtractionError(f"Ollama request failed: {e}") from e

    body = resp.json()
    content = body.get("message", {}).get("content")
    if not content:
        raise ExtractionError("Ollama returned no content.")

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"Ollama did not return valid JSON (model may not support structured output): {e}"
        ) from e


def _extract_via_openrouter(file_path: str) -> dict:
    """
    OpenRouter's primary interface is OpenAI-compatible chat completions, not Anthropic's
    native Messages API - even when the routed model is a Claude model. This means the
    request shape differs from _extract_via_anthropic in two real ways, not just the auth
    header:
      - PDFs go in as a `type: "file"` content block with a base64 data URL, not
        Anthropic's `type: "document"` block.
      - Forced tool selection uses OpenAI's `tools` + `tool_choice` function-calling shape,
        not Anthropic's `tool_choice: {"type": "tool", ...}`.
    Both were verified against OpenRouter's current docs before writing this, not assumed
    from the Anthropic-shaped code above.
    """
    if not settings.openrouter_api_key:
        raise ExtractionError("OPENROUTER_API_KEY is not configured.")

    data = _read_file_bytes(file_path)
    b64 = base64.standard_b64encode(data).decode("utf-8")

    request_body = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract this document's structured data per the tool schema."},
                    {
                        "type": "file",
                        "file": {
                            "filename": os.path.basename(file_path),
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                    },
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": EXTRACTION_TOOL["name"],
                    "description": EXTRACTION_TOOL["description"],
                    "parameters": EXTRACTION_SCHEMA,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": EXTRACTION_TOOL["name"]}},
    }

    headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
    if settings.openrouter_site_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url
    if settings.openrouter_app_name:
        headers["X-Title"] = settings.openrouter_app_name

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=request_body,
            headers=headers,
            timeout=settings.openrouter_timeout_seconds,
        )
        resp.raise_for_status()
    except httpx.ConnectError as e:
        raise ExtractionError("Could not reach OpenRouter (openrouter.ai) — check network connectivity.") from e
    except httpx.HTTPStatusError as e:
        # OpenRouter uses the same "credit balance too low" style 4xx as Anthropic direct -
        # surface the body, since it usually names the exact problem (auth vs. credits vs.
        # a model slug that doesn't exist).
        raise ExtractionError(f"OpenRouter API error ({e.response.status_code}): {e.response.text}") from e
    except httpx.HTTPError as e:
        raise ExtractionError(f"OpenRouter request failed: {e}") from e

    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise ExtractionError(f"OpenRouter returned no choices: {body}")

    tool_calls = (choices[0].get("message") or {}).get("tool_calls") or []
    if not tool_calls:
        raise ExtractionError("Model did not return a tool call (may not support forced tool use via OpenRouter).")

    arguments = tool_calls[0].get("function", {}).get("arguments")
    if not arguments:
        raise ExtractionError("OpenRouter tool call had no arguments.")

    try:
        return json.loads(arguments)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"OpenRouter tool call arguments were not valid JSON: {e}") from e


def _normalize(payload: dict) -> ExtractionResult:
    fields: list[ExtractedFieldResult] = []

    scalar_field_names = [
        "circular_number", "circular_date", "subject", "segment",
        "system_impacting", "impact_area", "effective_date", "summary",
    ]
    for name in scalar_field_names:
        entry = payload.get(name)
        if entry is None:
            # The model omitted this field entirely (not "null with low confidence" -
            # genuinely absent from its response). Treat that as a low-confidence result
            # rather than silently dropping it — an omitted field is otherwise
            # indistinguishable from success and never reaches the review queue, which is
            # exactly what let a badly-malformed classification show status=complete with
            # no red flags. See decisions.md #24.
            fields.append(ExtractedFieldResult(
                field_name=name,
                value=None,
                confidence=0.0,
                source_note="Field was not present in the model's response.",
            ))
            continue
        fields.append(ExtractedFieldResult(
            field_name=name,
            value=entry.get("value"),
            confidence=float(entry.get("confidence", 0.0)),
            source_note=entry.get("source_note"),
        ))

    for idx, item in enumerate(payload.get("key_points") or []):
        entry = item.get("point")
        if entry is None:
            continue
        fields.append(ExtractedFieldResult(
            field_name="key_point",
            value=entry.get("value"),
            confidence=float(entry.get("confidence", 0.0)),
            source_note=entry.get("source_note"),
            is_list_item=True,
            list_item_index=idx,
        ))

    return ExtractionResult(
        doc_type=payload.get("doc_type", "unknown"),
        doc_type_confidence=float(payload.get("doc_type_confidence", 0.0)),
        fields=fields,
        extraction_notes=payload.get("extraction_notes"),
        raw=payload,
    )
