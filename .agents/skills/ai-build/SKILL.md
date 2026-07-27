---
name: ai-build
description: Entry point for building anything with AI in this repo. Use whenever a user asks to build, implement, add, create, or change a feature, resource, endpoint, workflow, or any non-trivial piece of software. It is a thin dispatcher that routes the agent into the mandatory plan-then-build process owned by the plan-workflow skill (triage → overview → engineering doc → todo tracker).
---

# AI Build Workflow (Dispatcher)

Whenever a user asks to build, implement, add, create, extend, or change something,
do NOT jump straight to writing code. This skill is a **thin router** — it does
not own the methodology. The methodology, triage rules, templates, and review
gates all live in the `plan-workflow` skill.

## Your job in this skill

1. Recognize that the request is a build / change task.
2. Load the `plan-workflow` skill with the `skill` tool.
3. Follow `plan-workflow` end-to-end — starting with its **Step 0 Triage**, which
   decides whether the full 3-phase workflow runs, the compressed Phase 1 path
   applies, or the work is small enough to implement directly.
4. Do not duplicate `plan-workflow`'s rules here. If you are unsure about a
   gate, a phase boundary, a parallel-write rule, or a template, defer to
   `plan-workflow`.

## Boundary with `plan-workflow`

| Concern | Owner |
|---------|-------|
| Recognizing build/change requests | `ai-build` |
| Triage (skip / compressed / full) | `plan-workflow` Step 0 |
| Phase 1 / 2 / 3 methodology | `plan-workflow` |
| Review gates and approval semantics | `plan-workflow` |
| Optional deep multi-persona plan review | `plan-review` |
| Templates | `plan-workflow/templates/` |
| Worked example | `plan-workflow/examples/` |

Load `plan-workflow` now and continue from its Step 0.

> Optional: before implementation starts, load `plan-review` for a structured
> multi-persona review (CEO, CTO, CIO, CFO, Business Admin, Data Analytics,
> Data Scientist, Software Engineer, Engineering Manager) of the generated plan.
