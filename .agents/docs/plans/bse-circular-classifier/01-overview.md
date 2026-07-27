# Overview Plan: BSE Circular Classifier

- Status: Done (planning) — see 03-todo.md for open submission items
- Slug: `bse-circular-classifier`
- Form: Full (written retroactively — this project was built before `plan-workflow`
  was installed in this repo; see decisions.md #12 for how that gap was handled)
- Author (agent/model + date): Claude / 2026-07-27
- Date: 2026-07-27

## 1. Request & Problem

A backend engineer at Kuvera (mutual-fund platform, integrated with BSE StAR MF) currently
reads every BSE notice/circular by hand and decides, case by case: does this require any
change to our systems, and if so, where do I even look. Most circulars are purely
informational (holiday calendars, personnel notices). A minority describe a real operational
change — a new mandatory field in an order file format, a changed cutoff time, a new
validation rule — and those are the ones easy to miss buried in routine traffic. The request
(originating from the take-home assignment's "messy documents → structured, queryable data"
problem statement) was scoped to this real, work-motivated case rather than a generic
document type.

## 2. Goals & Non-Goals

- Goals:
  - Classify a circular: does it require a system change (`system_impacting`), which
    segment does it apply to (`segment`), and is the change backend, frontend, or both
    (`impact_area`) — each with a confidence score and a pointer to where in the document
    that judgment came from.
  - Route low-confidence classifications to a human review queue instead of trusting them
    silently.
  - Make classified circulars queryable (by segment, impact, date, freeform text).
  - (Stretch, added mid-build) Suggest which files in a local, private codebase likely need
    to change, for system-impacting circulars — real retrieval, not just classification.
- Non-goals (explicitly out of scope):
  - Automated ingestion from BSE's site (bot-detection blocked; manual upload for now).
  - Any document type other than BSE circulars/notices.
  - Confidence-threshold calibration against historical outcomes (no labeled corpus exists).
  - A UI — this is an API only.
  - Sending any part of a private codebase to a cloud LLM, under any configuration.

## 3. Product Features

- User stories:
  - As a Kuvera backend engineer, I want a system-impacting circular to land in a review
    queue with the specific clause that triggered the flag, so I don't have to re-read the
    whole PDF to find what changed.
  - As the same engineer, I want to confirm or correct a classification, so obviously-wrong
    guesses don't block on a human forever and get logged for future calibration.
  - As the same engineer, I want to ask "what's still open for backend, since January" and
    get a filtered list, not grep through PDFs.
- Key features: upload → classify → confidence-gated review → query; optional local repo
  change-suggestion for system-impacting circulars.
- UX / behavior notes: API-only (`/docs` for interactive testing); no UI exists — flagged
  as the highest-leverage next step in decisions.md and in this review's Business Admin
  lens.

## 4. Engineering Overview (surface level)

- Rough approach: PDF in → LLM classification (Claude native PDF understanding, or local
  Ollama on rendered page images) with forced structured output → EAV-style field storage
  → confidence-gated review queue → filtered query API.
- Affected areas of the system: `app/services/extraction.py` (LLM integration),
  `app/services/pipeline.py` (orchestration), `app/models.py` (EAV schema),
  `app/routers/*.py` (API surface), `app/services/repo_index.py` (local RAG for
  change-suggestion).
- Major building blocks / new components: dual LLM provider abstraction (Claude/Ollama)
  sharing one JSON schema; EAV field storage; confidence-gated review workflow; local-only
  code-retrieval index for the stretch feature.
- Dependencies: Anthropic API (optional), local Ollama (optional, and mandatory for the
  repo-suggestion feature specifically), PyMuPDF, FastAPI/SQLAlchemy.

## 5. Risks & Unknowns

- Confidence thresholds (0.75 classification, 0.35 retrieval similarity) are stated priors,
  not tuned against any labeled dataset.
- SQLite is the default `DATABASE_URL`; fine for a demo, wrong default for anything
  concurrent.
- No auth/authz on any endpoint (see 00-review-20260727.md, CIO/CTO lenses — BLOCKER for
  anything beyond a graded personal demo).

## 6. Open Questions for the User

- [x] Which use case to scope Option 3 around? → BSE circulars for Kuvera (resolved).
- [x] Which codebase should repo-suggestion index? → any local folder path, never uploaded;
      no public/real repo provided for this submission (resolved — see decisions.md #11).
- [ ] Deploy target and whether real auth gets added before/instead of a documented
      no-auth caveat — open, tracked in 03-todo.md.

## 7. Review

- Reviewer comments: see `00-review-20260727.md` (plan-review, 9 lenses) — 3 blockers,
  7 risks, 5 questions, 3 nits at time of that review; 2 of the blockers were code bugs,
  found and fixed same session.
- Resolution / changes made: `ReviewItem.original_value` added; malformed-LLM-shape
  exception now caught and surfaced as `ExtractionError` instead of leaving documents
  stuck in `PROCESSING`.
- Approval: [ ] Approved by user (date)
