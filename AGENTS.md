# AGENTS.md — itbisa-shop-stock-bot

**Source of truth is [`CLAUDE.md`](./CLAUDE.md).** Read it first and follow it for stack, layout, entry points, core logic, conventions, and the "flag before changing" list. This file only restates the non-negotiables so ChatGPT Codex has them inline.

## Non-negotiables

### Identity
- Author every commit and PR as `C - Furqon Aji Yudhistira <furqonajiy@gmail.com>`.
  - `git config user.name "C - Furqon Aji Yudhistira"`
  - `git config user.email "furqonajiy@gmail.com"`

### No AI/assistant references — anywhere
- Branch names, commit messages, PR titles/bodies, code, and comments must not name any AI assistant, carry a `Co-Authored-By` trailer, a "Generated with/by" line, model ids, or session links. History must read as human-authored.
- If a tool auto-appends such a footer to a PR body, edit the PR description to remove it and re-check.

### Workflow (every change)
- Branch `feature/<short-description>` off `main`. Documentation and the sync-marker update ride in the **same PR** as the related change — never a doc-only branch.
- Open a PR into `main`, then merge with a **merge commit (`--no-ff`)** — never squash, never fast-forward.
- The merge-commit title must be representative and end with the PR number, e.g. `Fix stock allocation rounding (#42)`.
- Avoid raw `<...>` placeholders in PR/issue bodies (GitHub strips them as HTML) — use `[..]`-style.

### Maintainer environment
- The maintainer runs Windows. Any CLI command handed over must be in **PowerShell** syntax.

### Sync marker
- A root file `YYYY-MM-DD_HHMM.txt` (WIB) marks the last sync. On every update to this repo, rename it to the current WIB timestamp.
