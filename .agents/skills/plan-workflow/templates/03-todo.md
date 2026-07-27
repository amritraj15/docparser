# Todo Tracker: <Feature Name>

- Status: Not started | In progress | Done
- Slug: `<feature-slug>`
- Linked engineering doc: `./02-engineering-doc.md`
- Last updated: <YYYY-MM-DD>

Derived from the Phase 2 Execution Strategy. Update continuously during
implementation. Statuses: `todo` | `in-progress` | `done` | `blocked`.

## Task Board

Mirrors the Phase 2 task table. Add commit/PR links as work lands.

| # | Task | Owner (agent) | Write scope | Status | Depends on | Link (commit/PR) |
|---|------|---------------|-------------|--------|------------|------------------|
| 1 | ... | ... | ... | todo | - | - |
| 2 | ... | ... | ... | todo | 1 | - |

## Per-Agent Sections

> Parallel safety: each agent edits ONLY its own section and only files inside
> its assigned write scope. Shared choke-point files (registries, bootstrap/DI,
> shared interfaces, generated code, migrations, package manifests, build config)
> are touched ONLY in the serial wiring phase by the assigned owner.

### Agent: <name>
- [ ] Task 1 — <short description>
- [ ] ...
- Notes / blockers:

### Agent: <name>
- [ ] Task 2 — <short description>
- Notes / blockers:

## Newly Discovered Work

> Purely additive tasks land here without re-approval. Anything that changes
> Phase 1 goals or Phase 2 architecture goes back to that phase for re-approval
> BEFORE being added here.

- [ ] ...

## Drift Check

> If you resumed work and the table above looks stale, reconstruct from recent
> commits + open PRs + working-tree status, update this file, THEN continue.

- Last reconciled against version control: <YYYY-MM-DD>

## Done Log

- <YYYY-MM-DD> — <what was completed> (agent, link)
