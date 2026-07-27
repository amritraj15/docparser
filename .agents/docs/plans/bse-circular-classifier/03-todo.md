# Todo Tracker: BSE Circular Classifier

- Status: In progress (core build done; submission items open)
- Slug: `bse-circular-classifier`
- Linked engineering doc: `./02-engineering-doc.md`
- Last updated: 2026-07-27

Reconstructed retroactively from the actual build history (this doc didn't exist during
the build — see decisions.md #12). Single agent, serial throughout — no per-agent split
needed.

## Task Board

| # | Task | Owner (agent) | Write scope | Status | Depends on | Link (commit/PR) |
|---|------|---------------|-------------|--------|------------|-------------------|
| 1 | Invoice-extraction skeleton (superseded) | Claude | `app/*` | done | - | - |
| 2 | Pivot to BSE circular classification schema | Claude | `extraction.py`, `models.py`, `routers/*`, `tests/*` | done | 1 | - |
| 3 | Dual LLM provider (Claude + local Ollama) | Claude | `extraction.py`, `config.py` | done | 2 | - |
| 4 | Local RAG repo-suggestion feature | Claude | `repo_index.py`, `routers/repo_index.py`, `routers/documents.py` | done | 3 | - |
| 5 | Fix: uncaught exception on malformed LLM shape | Claude | `extraction.py` | done | 4 | - |
| 6 | Fix: review correction destroyed original AI value | Claude | `models.py`, `pipeline.py`, `routers/review.py`, `schemas.py` | done | 4 | - |
| 7 | Install plan-workflow/plan-review/ai-build skills | Claude | `.agents/skills/*` | done | - | - |
| 8 | Retroactive Phase 1/2/3 docs (this doc + siblings) | Claude | `.agents/docs/plans/bse-circular-classifier/*` | done | 7 | - |
| 9 | Add authentication/authorization | — | `app/main.py`, new auth middleware | accepted — won't-fix for this submission (decisions.md #13) | - | - |
| 10 | Deploy to a reachable URL | — | infra / deploy config | todo | - | - |
| 11 | Push repository to GitHub | — | repo-level | todo | - | - |
| 12 | Review-queue UI (highest adoption lever per Business Admin lens) | — | new frontend | todo | - | - |

## Per-Agent Sections

Single-agent build — no disjoint-write-scope split was needed or used.

### Agent: Claude
- [x] Tasks 1–8 (see Task Board)
- [ ] Task 9 — add auth
- [ ] Task 10 — deploy
- [ ] Task 11 — push to GitHub
- [ ] Task 12 — review-queue UI
- Notes / blockers: Tasks 10–11 (deploy, GitHub push) block actual assignment submission
  per `00-review-20260727.md`'s Engineering Manager lens finding. Task 9 (auth) was
  reclassified from blocker to accepted risk on 2026-07-27 — see decisions.md #13.

## Newly Discovered Work

- [ ] Aggregate review corrections (predicted vs. corrected) into a report, so the
      confidence thresholds have something to eventually be tuned against (Data Analytics
      lens, `00-review-20260727.md`).
- [ ] Notification/alerting when a system-impacting circular is classified — currently
      requires polling `/review/queue` (Business Admin lens finding).
- [ ] Rate limiting / cost cap on `/documents` uploads (CFO lens finding).
- [ ] Reviewer-identity field on `ReviewItem` for audit trail (CIO lens finding).
- [ ] `AGENTS.md`, `templates/review.md`, and the `plan-workflow` worked example are still
      missing from the installed skills — author these before relying on `plan-workflow`
      for the *next* feature in this repo.

## Drift Check

- Last reconciled against version control: 2026-07-27 (this doc was authored by
  reconstructing from the conversation/build history directly, since no VCS history
  exists yet — repo hasn't been pushed to GitHub; see Task 11).

## Done Log

- 2026-07-27 — Core classification pipeline, dual LLM provider, local RAG repo-suggestion
  feature, and two bug fixes found via `plan-review` — all shipped same session (Claude).
