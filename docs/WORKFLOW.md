# Git & PR workflow

We use **GitHub Flow**: `main` is always deployable, all work happens on
short-lived branches merged back via PR.

## Branches
- `main` — protected, always green
- `feat/<short-name>` — new functionality
- `fix/<short-name>` — bug fixes
- `chore/<short-name>` — tooling, docs, config, no behavior change

## Commits
Conventional-commit-style prefixes, so history stays scannable and we can
generate a changelog from it later without rewriting anything:

`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`

## PRs
- One feature/fix per PR, opened against `main`
- CI (ruff + pytest) must pass before merge
- Squash merge, so `main`'s history is one commit per feature
- Update `docs/DEVLOG.md` with a short entry when a PR lands anything
  worth remembering (a new capability, not every small fix)
- Add a note under `docs/decisions/` only for choices that would be
  confusing to re-derive later (why this data source, why this library) —
  not for routine implementation details
