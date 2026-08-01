# decisions.md

A running log of the real calls made while building this, not a changelog.

---

## 1. Problem scope: invoices/receipts, not "any document"

**Decision:** Wire the extraction schema end-to-end for one document type (invoice-shaped:
vendor, invoice number, dates, total, line items) rather than building a generic
"extract anything" system.

**Alternatives considered:** A fully generic extractor where the user defines an arbitrary
schema per upload, or a multi-document-type classifier with a schema per type.

**Reasoning:** The hard, interesting part of this assignment is handling extraction
*uncertainty* gracefully — confidence scoring, partial failure, human review — not building
a schema-definition UI. Going deep on one document type and getting the uncertainty handling
right beats going wide on schema flexibility and getting everything shallow. The data model
(`ExtractedField` as name/value/confidence rows, not fixed columns) is schema-agnostic on
purpose, so adding a second document type later is a prompt/schema change, not a migration.

**What was cut:** Per-user custom schemas. The `record_invoice_extraction` tool schema is
the one shape currently wired up; the model is instructed to set `doc_type` and leave
inapplicable fields low-confidence/null if it's shown something else, so it degrades instead
of crashing on an off-type document, but it won't invent a *new* structured schema for it.

---

## 2. Extraction engine: Claude's native PDF understanding, not a custom OCR pipeline

**Decision:** Send the PDF directly to Claude as a base64 `document` content block and let
the model read layout, tables, and scanned text natively, instead of running Tesseract/OCR
and a separate layout parser ourselves.

**Alternatives considered:** Tesseract OCR + a hand-built layout parser (regex/positional
heuristics) + a separate LLM call over the extracted text; or a hybrid where OCR runs first
and only kicks in for scanned docs.

**Reasoning:** A hand-rolled OCR+layout pipeline is exactly the kind of thing that's
"good enough for demo PDFs" and falls over on the real-world mess (multi-column layouts,
tables spanning pages, rotated scans) — which is the part of this assignment explicitly
called out as the bar to clear. Claude's PDF understanding already handles that generalization
better than anything buildable from scratch in 5 days, and it collapses "OCR quality" and
"field extraction accuracy" into a single quality lever instead of two independently-failing
stages I'd have to debug separately.

**Tradeoff accepted:** This makes the system dependent on a single LLM vendor's PDF handling
and its per-request cost/latency, and there's no deterministic fallback if the API is down.
Mitigated by making failures per-document and retryable (`/documents/{id}/reprocess`) rather
than crashing a batch, but a production version would want a second-vendor or local-OCR
fallback path — flagged as a "beyond scope" item, not something silently ignored.

---

## 3. Structured output via forced tool-use, not "reply with JSON"

**Decision:** Force the model to call a single tool (`tool_choice: {"type": "tool", ...}`)
whose `input_schema` defines the extraction contract, rather than prompting "respond only
with JSON" and parsing the text response.

**Alternatives considered:** Prompt-based JSON with a regex/markdown-fence strip before
`json.loads`; a two-step "extract then validate" call.

**Reasoning:** Prompt-based JSON is the single most common source of silent extraction
failures — a stray sentence before the JSON, a trailing comma, a markdown fence the model
forgot to close. Tool-use input is schema-validated on Anthropic's side before it ever
reaches this codebase, which removes an entire failure class for free. It also makes the
"every field needs a confidence score" requirement structural (part of the schema) instead
of a prompt instruction the model can quietly ignore under pressure.

**What was cut:** A self-critique / second-pass verification call (extract, then ask the
model to check its own extraction against the source again). Would likely raise accuracy
further but doubles latency and cost per document; the confidence score + human review queue
does similar work for a fraction of the cost.

---

## 4. Per-field confidence + review queue, not per-document accept/reject

**Decision:** Every extracted field carries its own confidence score and source note. Fields
below the threshold go to a review queue as individual items, not as "this whole document
needs review."

**Alternatives considered:** A single document-level confidence score with an all-or-nothing
"accept/reprocess" gate; no review mechanism at all (trust the model).

**Reasoning:** This is the actual hard sub-problem of the assignment. A document is rarely
uniformly bad — it's usually one smudged number on an otherwise-clean invoice. Gating the
whole document on its worst field wastes a human's time re-verifying nine fields that were
already right. Per-field review means a reviewer's queue is exactly the set of things
actually in doubt.

**What was cut:** Confidence calibration/tuning against a labeled dataset. The threshold
(0.75) is a reasonable prior, not empirically tuned — there was no labeled corpus to tune
against in 5 days. Flagged as the first thing to do with real usage data.

---

## 5. Data model: EAV-style `ExtractedField` table, not one column per field

**Decision:** Store extracted fields as rows (`document_id`, `field_name`, `field_value`,
`confidence`, `source_note`) rather than a rigid `invoices` table with a fixed column per
field.

**Alternatives considered:** A proper relational `invoices` table with typed columns and a
separate `line_items` table with foreign keys; Postgres JSONB blob per document with no
per-field structure at all.

**Reasoning:** A fixed-column table is the "right" answer for one document type at fixed
scale, but it means a schema migration every time a new document type or field gets added —
which is likely the first real request after this ships. EAV rows keep line items and
scalar fields, across any future document type, in one queryable shape without migrations.
A pure JSONB blob was rejected because it loses the ability to query/filter/index individual
fields, and loses the per-field confidence/source_note structure that's the actual point of
this project.

**Tradeoff accepted:** EAV is worse for complex SQL joins and loses DB-level type safety
(everything's stored as text, cast in Python) — acceptable at this scale, a real constraint
if this had to serve high query volume. See decision 7.

---

## 6. Background processing: FastAPI `BackgroundTasks`, not Celery/RQ

**Decision:** Run extraction as a FastAPI `BackgroundTasks` job triggered on upload, with its
own isolated DB session, instead of standing up a real task queue.

**Alternatives considered:** Celery + Redis/RabbitMQ; RQ; a simple polling worker process.

**Reasoning:** A real queue is the correct answer past a single instance (retries, backoff,
concurrency control, worker scaling) — but standing up Redis/broker infra for a 5-day, single-
instance demo is infrastructure the grading rubric isn't asking for and that would eat build
time better spent on the extraction/review logic itself. `BackgroundTasks` gets the same
user-facing behavior (upload returns immediately, processing happens async) without it.

**What was cut:** Retries with backoff on transient API failures — currently a failure just
marks the document `failed` and a human (or a script) calls `/reprocess`. No automatic retry.
This is the most likely thing to build next in a real deployment, and the isolated-session
background task design was chosen specifically so it's a small change (swap the task
dispatch, not the pipeline logic) to move to a real queue later.

---

## 7. Query layer: Python-side filtering, not SQL-side casts, not vector search

**Decision:** `/query/documents` loads a document's fields and filters in Python (string
match, float cast with try/except, date-string comparison) rather than writing `CAST`/`ILIKE`
SQL against the EAV table, and does not do embedding-based semantic search.

**Alternatives considered:** SQL-side filtering with `CAST(field_value AS FLOAT)` per query;
adding an embeddings column + vector search (pgvector) for freeform natural-language queries
over the document corpus.

**Reasoning:** SQL casts over a text column work but are fragile across SQLite/Postgres and
add real complexity for a scale (a demo's worth of documents) where Python-side filtering
performs identically. Vector search is a genuinely good idea for "find documents about X"
freeform queries and was scoped out for time — it's called out explicitly in the README as
the first real feature to add, not silently dropped.

**What was cut:** Real semantic search; SQL-side indexed filtering. Both are the correct
move at production scale — noted as the concrete next step (promote `vendor_name`,
`total_amount`, `invoice_date` to indexed columns on `Document` once query volume matters,
since those are the fields actually being filtered on).

---

## 8. Dates assumed normalized to ISO-8601 by the model

**Decision:** Date range filtering (`date_from`/`date_to`) does lexical string comparison,
which only works correctly if `invoice_date` is in `YYYY-MM-DD` format. The extraction prompt
relies on the model's general instruction-following to produce that format rather than a
deterministic post-extraction normalization/validation step.

**Reasoning:** Claude reliably normalizes dates to ISO format when structured output is
requested in practice, and building a full date-parsing/normalization library (handling
`03/04/2026` ambiguity, non-US formats, "the 3rd of April" freeform text, etc.) is a
sub-project of its own.

**What was cut:** A deterministic date-normalization/validation pass after extraction. This
is a known sharp edge — flagged rather than hidden — and would be the second thing to harden
after retries, since silently-wrong date filtering is a worse failure mode than a document
that's merely stuck in the review queue.

---

## 9. Ollama support: optional local provider, not a replacement for Claude

**Decision:** Add a second extraction provider (`LLM_PROVIDER=ollama`) that routes through a
local Ollama vision model instead of Claude, selected by config rather than by a code change.
Claude remains the default.

**Alternatives considered:** Local-only (drop the cloud dependency entirely); a fully
pluggable provider abstraction with a registry/interface class for N providers.

**Reasoning:** The two providers need different inputs — Claude reads PDFs natively; local
vision models generally don't, so the Ollama path renders pages to images with PyMuPDF first.
Rather than build a speculative plugin interface for providers that don't exist yet, both
providers just produce the same plain dict payload shape (the same JSON schema is reused for
Claude's tool call and Ollama's `format` parameter) and share one `_normalize()` function.
That's the minimum abstraction that actually pays for itself right now.

**Tradeoff accepted:** Local vision models are noticeably weaker than Claude at dense
text/table extraction in practice, so running with Ollama means more fields land in the
review queue, not fewer — this is stated plainly in the README rather than papered over.
Multi-page documents also cost more locally: every page becomes a separate image sent to
the model, capped at `OLLAMA_MAX_PAGES` (default 5) specifically to keep context and latency
bounded, at the cost of silently ignoring pages beyond that cap.

**What was cut:** Automatic provider fallback (try Ollama, fall back to Claude on failure,
or vice versa). Each request commits to one provider for the whole run; switching is a config
change, not a runtime decision. A production system serving both privacy-sensitive and
accuracy-sensitive workloads would want per-request provider selection — noted as a natural
next step, not built here for time.

---

## 10. Pivot: generic invoice extraction → BSE circular classification for a mutual-fund platform

**Decision:** Replace the initial invoice/receipt extraction schema with a classification
schema purpose-built for BSE notices/circulars, aimed at a backend engineer at a mutual-fund
transacting platform (Kuvera, integrated with BSE StAR MF) who needs to know, per circular:
does it require a system change, is that change backend/frontend/both, and which market
segment does it apply to.

**Alternatives considered:** Keep the invoice schema as the flagship demo and treat circulars
as "just another document type" bolted on alongside it; build both schemas end-to-end to show
range.

**Reasoning:** The invoice extraction demo answered "can this system pull structured fields
out of a PDF" but never answered "for whom, and why does it matter." The circular use case is
a real problem from actual work, with a named person and a real cost to getting it wrong (a
missed system-impacting circular vs. a false positive that wastes an afternoon of
investigation). Rebuilding around it is a stronger answer to the assignment's product-thinking
and UX criteria than keeping a more generic but less-motivated demo. Building both schemas
was rejected — depth on one real use case beats breadth across two demo ones, consistent
with decision #1's original reasoning, just re-applied to a better-chosen target.

**What transferred unchanged:** The whole pipeline skeleton — upload → LLM classification with
per-field confidence → review queue for low-confidence fields → query. The EAV-style
`ExtractedField` table (decision #5) needed zero schema changes; only the extraction prompt,
tool schema, and the `_normalize()` field list changed. That's the payoff of that earlier
decision showing up in practice, not just in theory.

**What's explicitly NOT built (the real hard part, deliberately deferred):** The "suggested
place of change" — pointing a system-impacting circular at the actual file/module in
Kuvera's backend or frontend repo that likely needs updating — is not implemented. That
requires embedding a real codebase and doing semantic retrieval against the circular's
content, which is a legitimately new subsystem (and the first place actual RAG belongs in
this project — everything built so far is classification/extraction, not retrieval-augmented
generation). It's also not something a general take-home can wire to a real private company
repo. Scoped out as the clearly-labeled stretch goal, not silently dropped: the MVP stops at
classification (impacting/not, segment, backend/frontend/both) with confidence-gated human
review, which is a complete, defensible product on its own.

**Other things cut for the same reason:** Automated ingestion from BSE's own site — the
upload endpoint takes a manually-downloaded circular PDF, same as any other document, rather
than a scraper. BSE's site sits behind bot detection that a bare HTTP client won't clear, and
solving that (session-warmed headless browser, rate limiting) is an ingestion-automation
problem separable from the classification logic the assignment is actually evaluating.
Confidence-threshold calibration is unvalidated for this domain too — there's no labeled
corpus of "circulars that historically did/didn't require a system change" to tune the 0.75
threshold against, which is a real gap worth being upfront about rather than implying the
threshold is more rigorous than it is.

---

## 11. Repo change-suggestion: real RAG, scoped around a hard problem instead of a demo

**Decision:** Build a retrieval layer that suggests which files in a local codebase likely
need to change for a system-impacting circular — but scope the actual engineering effort
around the part that's genuinely hard (bridging regulatory language and code vocabulary,
and being honest when nothing matches) rather than around infrastructure (a vector DB, a
chunking framework) that would look more impressive in a demo and matter less in practice.

**The hard sub-problem, and why the obvious version fails:** A circular says "a new
mandatory field 'scheme_code_v2' shall be included in the order upload file format." Nothing
in that sentence looks like source code. Plain embedding similarity between regulatory prose
and code is not a well-aligned space — BSE says "UCC," codebases say `client_id`; BSE
describes a *file format change*, which usually lives in a schema/constants file, not the
business-logic function a naive "most similar text" search would surface first. The version
everyone would build — embed every file, cosine-similarity against the circular's text,
return the top match — produces a demo that works on one cherry-picked example and is
actively misleading the rest of the time, which is worse than not building it: a confidently
wrong suggestion sends an engineer down a dead end, whereas no suggestion just means they do
what they do today.

**What was actually built to address that, instead of just infra:**
- A small, explicit BSE-term-to-codebase-vocabulary glossary (`BSE_TERM_GLOSSARY` in
  `repo_index.py`) that expands the query before embedding — a heuristic patch for
  vocabulary drift, not a solved problem, but a real attempt at the actual gap rather than
  pretending raw similarity search bridges it.
- Filename/path heuristics (`FILE_TAG_HINTS`) that tag chunks as schema/constants/
  validation/api/model, biasing retrieval toward the kind of file a "new field in a file
  format" circular actually maps to, without needing a full per-language AST parser.
- **An explicit no-match branch.** Below `REPO_SIMILARITY_THRESHOLD`, the system reports
  "likely new functionality — no existing code found" instead of force-ranking a top-3 list.
  This is the same confidence-gating philosophy as the extraction pipeline's review queue,
  applied to retrieval instead of extraction — a low-confidence "I'm not sure" is more
  useful downstream than a confident wrong answer.

**Alternatives considered:** A real vector database (pgvector/Chroma/FAISS) instead of a
flat local JSON index with in-process cosine similarity; per-language AST-based chunking
(tree-sitter) instead of fixed-line-window chunking; a second LLM call to synthesize a
plain-English rationale per candidate instead of returning raw ranked snippets.

**Reasoning:** All three alternatives are real upgrades at real scale, and all three were
rejected for the same reason: at the data volume a local codebase index actually has, they
add engineering surface area without changing whether the system gives useful answers. A
flat JSON index with brute-force cosine similarity is transparent, debuggable, and fast
enough for a codebase's worth of chunks — the same "don't add infra the scale doesn't need"
call made in decision 7 for query filtering, applied here again. A rationale-synthesis LLM
call was cut specifically because it would mean sending code snippets to an LLM a second
time for a service that's supposed to guarantee code never leaves the machine — adding it
back only with a hard-locked local model, if at all, is the right sequencing, not a default.

**The confidentiality constraint, and why it shaped the architecture, not just the docs:**
The codebase this is meant to run against (Kuvera's actual backend/frontend) is private and
can't be uploaded anywhere — not to this repo, not to a public stand-in, not to a cloud
embeddings API. So the design point is a **local folder path**, configured via
`BACKEND_REPO_PATH`/`FRONTEND_REPO_PATH`, indexed and embedded entirely on the machine
running the app. This is enforced in code, not just convention: `_embed_texts()` in
`repo_index.py` only ever calls a local Ollama endpoint — there is no
`embedding_provider` setting, and `LLM_PROVIDER=anthropic` (used for circular
classification, which is fine to send to Claude since a BSE circular is a public document)
has zero effect on this path. The index cache (`repo_index/`) is gitignored, and the whole
feature is `REPO_SUGGESTION_ENABLED=false` by default so a deployed grading instance can
never accidentally index and expose anything, even if someone pointed the env vars at a
real path by mistake.

**Tested against:** Since neither a public stand-in repo nor the real private repo could go
in this submission, the test suite (`tests/test_repo_index.py`, `tests/test_repo_suggestions.py`)
exercises the indexer against small synthetic local directories created per-test, with a
deterministic fake embedding function standing in for the real Ollama call — the same
pattern already used for testing the classification LLM calls. This found a real bug during
development: when `REPO_INDEX_DIR` lives inside the repo root being indexed (exactly the
setup for testing this against docparser's own directory), the indexer was re-indexing its
own previous JSON output as source on every rebuild, reporting a spurious change every run.
Fixed by excluding the resolved index-cache path from the file walk — kept in
`decisions.md` because it's a good example of a real-world edge case that a synthetic,
too-clean test fixture almost hid: the bug only appeared once the index directory and the
repo root overlapped, which is the realistic case, not the convenient one.

**What's still cut, honestly:** No AST-based chunking (fixed-line windows lose some
structure a real parser wouldn't). No incremental file-watcher (re-index is a manual
`POST /repo-index/build`, not automatic on repo changes). No handling for a circular that
maps to genuinely distinct concerns across multiple unrelated files within the same
repo — results are ranked and deduplicated by file, but there's no explicit "these are two
separate issues" grouping. Each is a reasonable next step, not an oversight papered over.

---

## 12. Ran a structured multi-persona review against the actual implementation

**Decision:** Install the `plan-workflow`/`plan-review`/`ai-build` skills into this repo and
immediately apply `plan-review`'s nine-persona methodology retroactively against the actual
code and this decisions log, rather than only against a future plan. Full findings:
`.agents/docs/plans/bse-circular-classifier/00-review-20260727.md`.

**Reasoning:** The skill's own instructions assume a Phase 1/2 plan exists to review. This
project has neither — it used this decisions log instead, per the assignment's own required
artifact. Rather than skip the review because the input format doesn't match, the review
was pointed at the code + `decisions.md` directly, with that mismatch stated as the first
finding rather than silently worked around.

**What it found, concretely (not hypothetically):** Two real bugs, both fixed same-session:
1. `_normalize()` assumed every extracted field arrives as a `{value, confidence,
   source_note}` object. A model returning a bare string for a field (valid JSON, wrong
   shape) raised an unhandled exception that `pipeline.process_document`'s
   `except ExtractionError` didn't catch — leaving the document stuck in `PROCESSING`
   forever instead of landing in `FAILED` (visible, retryable). Fixed by wrapping
   normalization and re-raising as `ExtractionError`.
2. Correcting a review item overwrote `ExtractedField.field_value` directly, destroying the
   model's original prediction. That predicted-vs-corrected pair is exactly the labeled data
   this project would need to ever validate or retune the confidence thresholds decisions #4
   and #10 already admit are unvalidated priors. Fixed by adding `ReviewItem.original_value`,
   captured at creation and preserved through correction.

**Also surfaced, not yet fixed:** no auth/authz on any endpoint, no deployed URL yet, repo
not yet pushed to GitHub — see the review file for full severity classification and
reasoning on each.

**What was cut:** Full templates (`AGENTS.md`, `01-overview.md`, `02-engineering-doc.md`,
`03-todo.md`, `review.md`) referenced by the installed skills weren't provided in the
uploaded files and weren't reconstructed — the skills are installed and usable for their
core methodology (triage rules, review personas, severity classification) but a future
`plan-workflow` run in this repo will need those templates authored first.

---

## 13. No auth for the demo submission — accepted, not fixed

**Decision:** Leave every endpoint unauthenticated for this submission, reclassifying the
CIO/CTO "no AuthN/AuthZ" finding from `00-review-20260727.md` as an accepted risk rather
than a blocker to resolve before submitting.

**Alternatives considered:** Add a minimal API-key gate before deploying (was the review's
original recommendation); ship with the explicit README caveat instead (chosen).

**Reasoning:** This is a graded take-home demo, not a production deployment carrying real
regulatory data — reviewed by a small, known set of people, time-boxed, and disposable
after grading. Precedent: public take-home/demo submissions in this space commonly ship
without auth for the same reason (e.g. https://github.com/interviewstreet/hiring-agent).
Building real auth would spend build-time on a concern that doesn't change whether the
core submission — classification quality, review-queue design, the RAG retrieval piece —
is any good, which is what's actually being evaluated.

**What makes this different from silently ignoring the finding:** the risk is still true
and still stated plainly — the README's security-note section stays, unmodified, so anyone
who deploys this beyond the grading window knows exactly what they're accepting. The
severity in `00-review-20260727.md` is updated to reflect this decision, not deleted.

**What was cut:** Any auth implementation for this submission. If this project continues
past grading — e.g. actually piloted internally at Kuvera — this reclassifies back to a
blocker immediately; the precedent above justifies a graded demo, not a real deployment
touching real circulars or a real private codebase.

---

## 14. Deploy target: Render over Railway

**Decision:** Deploy to Render, using a checked-in `render.yaml` for reproducible
Infrastructure-as-Code setup, rather than Railway.

**Alternatives considered:** Railway (the platform originally discussed); Fly.io.

**Reasoning:** As of mid-2026, Railway no longer has a free tier — it removed its prepaid
credit option earlier this year and now requires a card on file even for minimal usage.
Render still offers a genuine free tier (750 instance-hours/month, no card required) with
first-class background-worker/web-service support, which fits this project's
`BackgroundTasks`-based architecture and — more importantly for a graded take-home — doesn't
put a billing surface in front of something that shouldn't cost the reviewer or the
submitter anything to exist. Fly.io wasn't seriously considered: its edge-latency strengths
(the reason to pick it) don't matter for a single-reviewer demo with no geographic spread.

**Tradeoffs accepted, stated plainly rather than discovered later:** Render's free tier
sleeps after 15 minutes idle (30–50s cold-start on wake) and its disk is ephemeral — SQLite
and uploaded PDFs do not survive a redeploy or restart. Both are fine for a time-boxed
grading window and explicitly documented in the README rather than left as a surprise.
`REPO_SUGGESTION_ENABLED` stays `false` in the deployed config specifically so the public
demo instance can never be pointed at a real codebase, deliberately independent of decision
#11's local-only design — belt and suspenders.

**What was cut:** A managed Postgres addon for the deployed instance (Render offers one
free, but it itself expires after a period of inactivity, trading one persistence problem
for a shorter-lived one) — SQLite-on-ephemeral-disk was judged good enough for a grading
window, with the swap documented as a one-line `DATABASE_URL` change if persistence turns
out to matter.

---

## 15. Deploy failure: pin Python version, don't chase the dependency

**Decision:** Add `.python-version` (and `PYTHON_VERSION` in `render.yaml`) pinning Python
to `3.12.7`, rather than upgrading `pydantic`/`pydantic-core` to a newer release.

**What happened:** The first real Render deploy failed at `pip install`.
`pydantic-core==2.23.4` has no prebuilt wheel for Python 3.14 (Render's current default),
so pip fell back to building it from source via `maturin`/Rust — which then failed too,
because Render's build sandbox has a read-only `cargo` registry cache dir. Verified directly
(not just inferred from the log): `pip download pydantic-core==2.23.4 --python-version 312
--only-binary=:all:` succeeds with a clean `cp312` wheel; the same command for `--python-
version 314` finds no matching distribution at all — the earliest version with a 3.14 wheel
is 2.35.0.

**Alternatives considered:** Bump `pydantic`/`pydantic-core` to a version with 3.14 wheels
(2.35.0+, or latest 2.47.0); let Render pick whatever Python it defaults to and hope future
releases fix it.

**Reasoning:** Pinning the Python version is the smaller, more surgical change — one file,
zero risk of a `pydantic` major-version-adjacent behavior change rippling into
`pydantic-settings` or FastAPI's request validation, which are exercised by all 46 existing
tests and weren't worth re-verifying under time pressure the night before a deploy. Bumping
the dependency is the more forward-looking fix (3.12 support won't be default forever) and
is a reasonable follow-up once there's time to re-run the full suite against a newer
`pydantic` deliberately, not as a same-night reaction to a failed build.

**What this is a good example of:** exactly the kind of environment-mismatch failure that
only shows up at actual deploy time, not in local dev (this sandbox's Python version
happened to already have wheels available) or in the test suite (which never touches a
build/install step). Recorded here rather than just fixed silently, because "local tests
green" and "deployable" turned out to be different claims.

---

## 16. Post-deploy smoke test: cost-ordered, not just present/absent

**Decision:** Add `scripts/smoke_test.sh` — a single script that checks a live deployment,
ordered from free to costly, with the LLM-spending end-to-end check opt-in via a second
argument rather than always running.

**Alternatives considered:** A pytest-based integration suite that hits the live URL
instead of a shell script; always running the full end-to-end check.

**Reasoning:** Always running the real upload→classify check would spend an API call every
time someone just wants to confirm the instance is awake — the wrong default for something
likely to be re-run casually after every deploy. Ordering checks free-to-costly and gating
the costly one behind an explicit PDF argument means "is it up" and "does classification
actually work" are two different, deliberately separable questions. A pytest suite was
rejected mainly for portability — this needs to run from anyone's shell against a URL with
zero setup, not require the project's virtualenv/dependencies to be installed locally.

**Verified, not just written:** tested against a locally running instance in this session —
once confirming all 8 free checks pass against the real app, and once with a real PDF
upload against an instance with no API key configured, to confirm the polling/failure-
detection logic surfaces the actual error (`ANTHROPIC_API_KEY is not configured`) instead of
hanging or crashing on unexpected JSON shape.

**What was cut:** No CI wiring (e.g. a GitHub Action that runs this automatically after
every deploy) — this is a manual, on-demand check for a single-instance demo, not a
pipeline. A reasonable next step if this project outlives the grading window.

---

## 17. Review-queue UI: single static HTML file, served by the API itself

**Decision:** Build the review-queue UI as one self-contained HTML file
(`app/static/index.html`, vanilla JS, no build step, no framework) served directly by
FastAPI at `/`, rather than a separate frontend project/deploy.

**Alternatives considered:** A React/Vite app deployed separately (e.g. Vercel, per the
earlier discussion of why Vercel doesn't fit the *backend*); a server-rendered template
(Jinja2) instead of a client-side fetch-driven page.

**Reasoning:** This is the highest-leverage gap flagged in `00-review-20260727.md`
(Business Admin lens) — the review queue was the one place a human actually had to
interact with the system, and it was curl-only. A separate frontend deploy doubles the
number of moving parts (two repos or two deploy targets, a CORS story to get right, a
second thing that can be down) for a UI whose entire job is calling five existing JSON
endpoints. Serving one static file from the same FastAPI app means the existing Render
deploy *is* the UI deploy — no new infra decision needed at all. A Jinja2/server-rendered
approach was rejected because the confirm/correct/upload interactions are genuinely
client-side (optimistic-feeling removal from the queue, drag-and-drop, polling a
processing document) and fighting a template engine for that isn't simpler than fetch().

**Design intent, not incidental:** the subject is an internal, compliance-adjacent tool for
an engineer clearing a queue — not a marketing surface — so the visual language is a
ledger/register (hairline dividers, mono data columns, a stamp motif on confirm/correct)
rather than a generic cards-and-shadows dashboard. The stamp is the signature element:
circulars are literally paper notices that get stamped in a real review process, so it's
earned by the subject rather than decorative.

**Verified, not just written:** ran a live server (extraction mocked, since this sandbox
has no real Anthropic key) and drove the actual HTTP calls the JS makes — upload, the
document-detail shape the field list renders from, the review-queue shape the entry
cards render from, both `confirm` and `correct` resolutions (confirmed `original_value`
is preserved and `current_value`/confidence update correctly on correction), and the
415/400 validation paths the dropzone's error handling depends on. All field names in the
JS were checked against real API responses, not assumed. A regression test
(`tests/test_ui.py`) confirms the route serves and references the right API paths, so this
stays covered by the normal test suite going forward, not just this session's manual check.

**What was cut:** No authentication on this page either — same accepted-for-demo call as
decision #13, inherited automatically since it's served by the same unauthenticated app.
No real-time updates (the queue only refreshes after an action or a manual reload — no
websocket/SSE); acceptable at demo scale, a real next step if this sees actual multi-user
use. No pagination on the review queue or document list — fine at demo volume, a genuine
gap at real volume.

---

## 18. Completed the plan-workflow skill install: AGENTS.md, review template, worked example

**Decision:** Author the three pieces flagged as missing back in decision #12 —
`AGENTS.md` (bootstrapped via real discovery of this repo, not guessed), `templates/
review.md` for `plan-review` (extracted from the actual structure `00-review-20260727.md`
already used, not invented fresh), and a worked example (`examples/user-deactivation/`)
demonstrating the parts of `plan-workflow` this project's own build never exercised.

**Why `user-deactivation` and not a docparser feature as the worked example:** this
project was single-agent and serial throughout (correctly — see decisions.md #10's
Engineering Manager finding, confirmed N/A), so its own Phase 1/2/3 docs never had to
demonstrate the interface-first execution pattern, a choke-point table, or per-agent
sections in a todo tracker under real parallel writes. A worked example that only shows
what this project already did would teach half the methodology. User deactivation is
small, realistic, and naturally has a genuine choke point (a migration + an auth
middleware check that every other piece of the feature depends on) — reused directly from
`plan-workflow`'s own SKILL.md wording as the archetypal parallel-then-wire case.

**`AGENTS.md`, discovered not guessed:** walked the actual repo (`find`, `cat
requirements.txt`, `cat .python-version`) rather than writing a plausible-sounding generic
doc. Findings worth flagging here because they're the kind of thing that's easy to miss on
a casual read: no lint/format tool is configured anywhere in the repo (a real gap, noted
plainly rather than papered over), and there is genuinely no migration tool — `AGENTS.md`
names the exact place this bit the project once already (`ReviewItem.original_value`,
decisions.md #12) so it isn't rediscovered the same way twice.

**What's still not done:** per `plan-workflow`'s own Step 0 instructions, a newly
bootstrapped `AGENTS.md` gets a lightweight human review before being trusted — "show a
short summary, ask what's wrong or missing." That review hasn't happened yet; this decision
entry is the build, not the sign-off.

---

## 19. Persistence fix: Neon Postgres over Render's free Postgres or Supabase

**Decision:** Make `DATABASE_URL` on the deployed Render instance point at a Neon
(neon.tech) free Postgres database, instead of relying on the SQLite default that
`render.yaml` originally shipped with.

**What prompted this:** confirmed directly against Render's current docs (not assumed):
free-tier services have an ephemeral filesystem wiped on *every* restart, redeploy, or
15-minute-idle spin-down — not just occasionally between deploys, which is what the
earlier README wording implied. In practice this meant the deployed instance was silently
losing all uploaded documents and classifications on a regular, expected basis, not as a
rare edge case. This is a correction to decision #14's original framing, not a new problem.

**Alternatives considered:** Render's own free managed Postgres (one click, same
dashboard); Supabase free tier.

**Reasoning:** Render's free Postgres expires 30 days after creation (with a 14-day grace
period) — fine for the grading window specifically, but trades one persistence problem for
a shorter-lived one, and decision #14 already flagged this tradeoff without committing to
it. Neon doesn't have that expiry. Supabase was rejected for the same reason Supabase
tends to get rejected in this project's decisions: it bundles auth, storage, and realtime
that nothing here uses, and the entire point of this fix is adding exactly one thing
(durable Postgres), not a second product surface to reason about.

**Implementation, verified not assumed:**
- `psycopg2-binary==2.9.10` added to `requirements.txt` — checked directly (`pip download
  ... --python-version 312 --only-binary=:all:`) that it has a real prebuilt `cp312`
  manylinux wheel, specifically because the last dependency added to this project
  (`pydantic-core`) did NOT have a wheel for the deployed Python version and broke the
  build (decisions.md #15) — not repeating that mistake blind twice.
- `app/database.py` now normalizes a legacy `postgres://` URL scheme to `postgresql://`
  before handing it to SQLAlchemy, which rejects the old scheme outright (a real gotcha
  some copy-pasted connection strings — Heroku-style ones especially — still carry).
  Covered by `tests/test_database.py`, including that this doesn't over-replace if the
  substring appears again later in the URL (e.g. inside a password).
- `render.yaml`'s `DATABASE_URL` switched from a hardcoded SQLite value to `sync: false`
  (same pattern already proven working for `ANTHROPIC_API_KEY`), so the real Neon
  connection string lives only in Render's dashboard, never committed.
- Full test suite re-run after adding the dependency: 51 passed (47 + 4 new for the URL
  normalization).

**What this does NOT fix, stated plainly rather than implied:** uploaded PDF *files*
themselves still live on local disk and are still lost on every restart — only the
database-backed classification data (documents, extracted fields, review items) persists
with this change. Fixing file persistence too would mean object storage (S3/R2) and is a
separate piece of scope, not bundled into this fix.

---

## 20. Third LLM provider: OpenRouter, built as its own code path, not a key-swap

**Decision:** Add `LLM_PROVIDER=openrouter` as a third extraction provider alongside
Anthropic direct and local Ollama, implemented as `_extract_via_openrouter()` — its own
function with its own request shape, not an attempt to point the existing Anthropic SDK
client at OpenRouter's base URL.

**Why not just swap the base_url on the Anthropic SDK:** OpenRouter's primary,
documented interface is OpenAI-compatible chat completions — even for routing to Claude
models. There is a route where Claude Code CLI points the Anthropic SDK's `base_url` at
OpenRouter directly, but whether that passthrough correctly handles this project's two
Anthropic-specific mechanisms (native `document` content blocks for PDFs, and forced
`tool_choice: {"type": "tool", ...}`) isn't something documented as a general guarantee
for third-party use of the raw SDK — and there was no OpenRouter key available to verify
it empirically either way. Rather than ship a maybe-correct shortcut and find out from a
silently-wrong classification later, this was built against OpenRouter's actually-documented
API shape instead.

**What's genuinely different in the OpenRouter path, verified against current docs before
writing any code:**
- PDFs go in as a `{"type": "file", "file": {"filename": ..., "file_data": "data:application/pdf;base64,..."}}`
  content block — OpenAI's file-input shape, not Anthropic's `document` block.
- Forced tool selection uses OpenAI's function-calling shape (`tools: [{"type": "function", ...}]`,
  `tool_choice: {"type": "function", "function": {"name": ...}}`), reusing the exact same
  `EXTRACTION_SCHEMA` already shared between the Anthropic and Ollama paths — the schema
  didn't need to change, only how it's wrapped for each provider's calling convention.
- Implemented via raw `httpx` calls (same pattern as the Ollama path), not the `openai`
  SDK — avoids adding a second HTTP client library for what's a handful of fields in a
  JSON request body.

**Explicitly not a privacy option:** stated in both the README and here, since it would be
easy to mentally lump this in with the local Ollama path as "the non-Anthropic option."
OpenRouter is a cloud aggregator; circulars sent through it leave the machine the same way
they do with Anthropic direct. Only Ollama keeps documents local.

**Tested against real API shapes, not just the happy path:** `tests/test_openrouter_extraction.py`
covers the PDF-as-file-block and forced-tool-call request shape, both optional headers
(`HTTP-Referer`/`X-Title`), a missing API key, a connection failure, a credit-balance-style
4xx error (mirroring the real "credit balance too low" error this project already hit with
direct Anthropic — confirming the error body surfaces in `Document.error_message` rather
than being swallowed), a response with no tool call at all, and malformed tool-call
arguments. 8 new tests, 59 total, all green.

**What was cut:** No automatic fallback between providers (try OpenRouter, fall back to
Anthropic, etc.) — same reasoning as decision #9's original scope cut for Ollama/Anthropic,
just extended to the third option. Provider selection stays a config choice per deployment,
not a runtime decision. The `OPENROUTER_MODEL` default is a best-effort current slug, not
verified against a live call (no key available) — the README and `.env.example` both flag
that these slugs drift and point at `openrouter.ai/models` to confirm before relying on it.

**Addendum — model slug verified, default switched to an alias:** searched
`openrouter.ai/models` directly rather than leaving the uncertainty open. The original
default (`anthropic/claude-sonnet-4.6`) turned out to be correct — but OpenRouter also
publishes `anthropic/claude-sonnet-latest`, an alias that always redirects to whichever
Sonnet-class model is current. Switched the default to that alias, since it removes the
"slugs drift, go check" caveat entirely rather than just resolving it once. Documented the
tradeoff plainly: the alias means the model you get can change out from under you on a
future Anthropic release, which is desirable for "stay current with no maintenance" but
wrong for anyone who needs reproducible behavior across a model upgrade — for that, pin an
exact slug instead, and both `.env.example` and the README say so.

---

## 21. Local Supabase as an optional local-dev database — no code change required

**Decision:** Document `supabase start` (the Supabase CLI's local Docker stack) as a
supported way to get a persistent local Postgres for development, as an alternative to the
default SQLite file — without changing any application code, because `DATABASE_URL` has
been Postgres-agnostic since decision #19.

**Why this doesn't contradict decision #19's rejection of Supabase:** that decision was
about the *deployed* instance specifically — Supabase's bundled auth/storage/realtime was
the wrong tradeoff for adding exactly one thing (durable Postgres) to a public deployment.
Local development is a different context: the bundle is disposable, runs on the
developer's own machine, and Supabase's Studio UI (a genuine, real benefit not available
from Neon) makes it easy to browse the `documents`/`extracted_fields`/`review_items` tables
directly while developing — which is exactly the "history of uploads" visibility being
asked for here. Same tool, different tradeoff, because the context changed, not because
decision #19 was wrong.

**Verified, not assumed:** the default local connection string
(`postgresql://postgres:postgres@localhost:54322/postgres`) and Studio URL
(`http://localhost:54323`) were confirmed directly from a real `supabase start` output
found in current documentation, not recalled from memory — connection-string mismatches
are exactly the kind of thing worth checking given this project's own history with
environment-specific surprises (decisions #15, #19).

**What was cut:** No `docker-compose.yml` checked into this repo for a minimal
Postgres-only local stack. `supabase start -x gotrue,storage-api,realtime,imgproxy,edge-runtime`
already gets a lighter footprint through the official CLI without maintaining a second,
parallel local-infra definition that could drift from what the CLI actually provisions.
`supabase/` (the CLI's local config/state) is gitignored — it's a per-developer local
provisioning choice, not shared project configuration, consistent with how `.env` and
`uploads/` are already treated.
