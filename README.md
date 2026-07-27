# BSE Circular Classifier

Reads a BSE (Bombay Stock Exchange) notice/circular and tells a backend engineer, at a
glance: **does this require a system change, is it backend or frontend, and which market
segment does it apply to.** Built for a mutual-fund transacting platform integrated with
BSE StAR MF (the motivating case: Kuvera).

Full design reasoning, alternatives considered, and what was deliberately cut — including
why the project pivoted from a generic invoice-extraction demo to this — see
[`decisions.md`](./decisions.md).

## Who this is for

An engineer at a mutual-fund platform who currently has to read every BSE circular by hand
and decide "does this break something in our system, and where do I even look." Most
circulars are purely informational (holiday calendars, personnel notices). A few describe a
real operational change — a new mandatory field in an order file, a changed cutoff time, a
new validation rule — and those are exactly the ones easy to miss buried in routine traffic.

## What it does

1. **Upload** a circular PDF (BSE's site sits behind bot detection, so for now this expects
   a manually-downloaded circular — see `decisions.md` decision 10 for why automated
   ingestion was scoped out).
2. It's sent to an LLM (Claude or a local Ollama model — your choice), which classifies it:
   `segment` (mutual_fund / equity / debt / ...), `system_impacting` (true/false),
   `impact_area` (backend / frontend / both / none), plus a summary and the specific
   `key_points` that justify the classification — **each with its own confidence score and
   a note on where in the document it came from**, not a flat verdict.
3. Any classification below the confidence threshold (default 0.75) is routed to a **review
   queue** instead of silently trusted — a human confirms or corrects it.
4. Once classified, circulars are **searchable**: by segment, by whether they're
   system-impacting, by impact area, by effective date, or by freeform text.

The core bet: classification is never all-or-nothing. If the model is confident this is a
mutual-fund circular but unsure whether it's backend or frontend, that's the one thing that
should reach a human — not a demand to re-verify the whole classification from scratch.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, or see Ollama section below
uvicorn app.main:app --reload
```

API docs (interactive): http://localhost:8000/docs

No Postgres needed to try it — defaults to a local SQLite file. Set `DATABASE_URL` in `.env`
to point at Postgres for anything beyond a local demo.

### Security note — no auth, by design, for now

**Every endpoint here is unauthenticated.** This is a single-tenant local/demo build; there
is no API key, session, or user model. That's a deliberate, explicitly accepted scope cut
for a time-boxed graded submission — not an oversight, and not unusual for this kind of
demo (see e.g. [interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent))
— but it means: don't deploy this to a URL you'd consider production-adjacent, and don't
point `BACKEND_REPO_PATH`/`FRONTEND_REPO_PATH` at a real codebase on any instance reachable
by anyone other than you. See `decisions.md` #13 and `00-review-20260727.md` (CIO/CTO
lenses) for the full reasoning and what adding real auth would need to cover.

### Running fully local, with Ollama (no API key, no cloud calls)

The extraction provider is swappable — `LLM_PROVIDER=ollama` routes every classification
through a local Ollama model instead of Claude, so the whole pipeline can run offline.

1. **Install Ollama**: https://ollama.com/download
2. **Pull a vision-capable model** (it needs to *see* the document, and ideally support
   structured/JSON output):
   ```bash
   ollama pull llama3.2-vision
   # alternatives: qwen2.5vl, minicpm-v, llava
   ```
3. **Start the Ollama server** (if it isn't already running as a service):
   ```bash
   ollama serve
   ```
4. **Point the app at it** — in `.env`:
   ```bash
   LLM_PROVIDER=ollama
   OLLAMA_HOST=http://localhost:11434
   OLLAMA_MODEL=llama3.2-vision
   ```
5. **Run the app as normal:**
   ```bash
   uvicorn app.main:app --reload
   ```
   `ANTHROPIC_API_KEY` isn't needed at all in this mode.

**What's different under the hood:** Claude reads PDFs natively; most local vision models
don't, so the Ollama path renders each PDF page to a PNG (via PyMuPDF) and sends those images
to the model instead, capped at `OLLAMA_MAX_PAGES` pages (default 5) to keep context/latency
bounded on long documents. Structured output is requested via Ollama's JSON-schema `format`
parameter — the same schema used for the Claude tool call, so both providers return
identically-shaped data and everything downstream (confidence scoring, review queue, query)
is unaffected by which one you're using.

**Tradeoffs to know going in:**
- Local vision models are meaningfully weaker at dense-text extraction than Claude — expect
  more fields to land in the review queue, not fewer.
- If you get "Ollama did not return valid JSON" errors, confirm your Ollama version supports
  the `format` JSON-schema parameter and that the model you pulled supports vision input.
- No cost, no network dependency, and circulars never leave your machine — the right
  tradeoff if that matters more than accuracy for your use case.

### Run the tests

```bash
pytest -q
```
24 tests, all with the LLM call mocked — no network access or API key needed to run the
suite. `tests/test_extraction.py` covers the real normalization logic and failure paths
(missing file, empty file, missing API key) against the actual extraction module.

## API walkthrough

**Upload a circular** (kicks off classification as a background task):
```bash
curl -F "file=@20260722-30.pdf" http://localhost:8000/documents
# -> {"id": "...", "status": "uploaded", ...}
```

**Check status / see the classification:**
```bash
curl http://localhost:8000/documents/{id}
```
`status` is one of `uploaded → processing → (needs_review | complete | failed)`.

**See the controlled vocabulary** (for a dropdown, per the original ask):
```bash
curl http://localhost:8000/reference/segments
curl http://localhost:8000/reference/impact-areas
```

**See what needs human review:**
```bash
curl http://localhost:8000/review/queue
```

**Resolve a review item:**
```bash
curl -X POST http://localhost:8000/review/{review_id}/resolve \
  -H "Content-Type: application/json" \
  -d '{"action": "correct", "corrected_value": "frontend", "reviewer_note": "actually a UI-only change"}'
```
`action` is `confirm` (the classification was right) or `correct` (override it). A corrected
value is written back with confidence 1.0 — it's now ground truth.

**Query classified circulars:**
```bash
curl "http://localhost:8000/query/documents?segment=mutual_fund&system_impacting=true"
curl "http://localhost:8000/query/documents?impact_area=backend&date_from=2026-01-01"
curl "http://localhost:8000/query/documents?text=scheme%20code"
```

**Retry a failed classification** (e.g. after a transient API error, or a fixed API key):
```bash
curl -X POST http://localhost:8000/documents/{id}/reprocess
```

### Repo change-suggestion (optional, local-only, off by default)

For a `system_impacting=true` circular, this suggests which files in a **local** codebase
likely need to change — using real retrieval (embed the repo, embed the circular's key
points, rank by similarity), not just classification. It's off by default
(`REPO_SUGGESTION_ENABLED=false`) so a shared/deployed instance never indexes a real codebase
by accident.

**This never sends code to a cloud API, even if `LLM_PROVIDER=anthropic` above.** Embedding
is hard-locked to a local Ollama model in `app/services/repo_index.py` — there's no
`embedding_provider` setting to flip. A circular is a public regulatory document, fine to
send to Claude; your codebase isn't, and this is enforced in code, not just a setting you
could get wrong.

**To try it locally:**
```bash
# 1. Pull a local embedding model
ollama pull nomic-embed-text

# 2. In .env:
REPO_SUGGESTION_ENABLED=true
BACKEND_REPO_PATH=/path/to/your/local/backend/checkout
FRONTEND_REPO_PATH=/path/to/your/local/frontend/checkout

# 3. Build the index (one-time, or after the codebase changes meaningfully)
curl -X POST "http://localhost:8000/repo-index/build?target=backend"
curl -X POST "http://localhost:8000/repo-index/status"

# 4. For a system-impacting document already classified:
curl -X POST http://localhost:8000/documents/{id}/suggest-changes
```

No real repo handy to try this against? Point `BACKEND_REPO_PATH` at this project's own
directory (`docparser/`) — it's a real local folder with real Python files, which is exactly
what the indexer needs to exercise the walk/chunk/embed/search path end to end, even though
it won't produce meaningful *matches* for a mutual-fund circular.

If nothing in the codebase clears the similarity threshold, the response says so explicitly
(`"matched": false`, with a reason) instead of forcing a guess — that's deliberate, not a
missing feature. See `decisions.md` (decision 11) for the reasoning behind that, and for the
real limitations: fixed-line-window chunking (no per-language parsing), no file-watcher
(re-run `/repo-index/build` manually after real changes), and no handling for a circular
that touches multiple unrelated files at once.

## Architecture

```
Upload (PDF) ──► Storage (local disk / uploads dir)
                        │
                        ▼
        Background task: LLM classification
        (Claude native PDF understanding + forced tool-use, OR
         Ollama on page images + JSON-schema structured output)
                        │
                        ▼
        Persist: Document + ExtractedField rows
        (EAV-style: field_name/field_value, not one
         rigid column per document type — see decisions.md #5)
                        │
                        ▼
        Fields below confidence threshold ──► ReviewItem queue
                        │
                        ▼
        Document status: needs_review | complete | failed
                        │
                        ▼
        /query/documents — filter by segment, system_impacting,
        impact_area, effective date, freeform text
```

Key files:
- `app/services/extraction.py` — the LLM integration: schema (segment, system_impacting,
  impact_area, key_points — each with confidence/source_note), prompt, both providers,
  response normalization.
- `app/services/repo_index.py` — the RAG piece: local-only codebase indexing and retrieval
  for the change-suggestion feature (decision 11). Hard-locked to a local embedding model.
- `app/services/pipeline.py` — orchestrates upload → classification → persistence → review
  routing. Failures are per-document and retryable, never crash a batch.
- `app/models.py` — `Document`, `ExtractedField` (EAV-style), `ReviewItem`.
- `app/routers/` — `documents.py` (upload/status/suggest-changes), `review.py`
  (queue/resolve), `query.py` (structured + freeform search), `reference.py`
  (segment/impact-area dropdown values), `repo_index.py` (build/status for the local index).

## What's deliberately not here

See `decisions.md` (decisions 10-11 especially) for the full reasoning:
- **No automated ingestion from BSE.** Their site sits behind bot detection; the upload
  endpoint takes a manually-downloaded PDF for now. A scheduled, session-warmed scraper is
  the natural next step, kept out of scope on purpose.
- **Repo change-suggestion is real but has real limits.** Fixed-line-window chunking
  instead of per-language AST parsing; no incremental re-index on repo changes (manual
  `/repo-index/build`); no grouping when one circular touches several unrelated files.
  Deliberately kept transparent (raw ranked snippets, no LLM-generated rationale) rather
  than sending code through a second model call.
- **No confidence calibration against historical outcomes.** The 0.75 classification
  threshold and the 0.35 retrieval-similarity threshold are reasonable priors, not tuned
  against labeled historical data — there wasn't one available to tune against.
- **No UI.** This is an API. The review queue — the one place a human actually has to act —
  is the highest-value place to add one next.
