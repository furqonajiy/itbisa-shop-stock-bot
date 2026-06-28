#!/usr/bin/env python3
"""
cleanup_branches.py
-------------------
Branch hygiene for this repo — remove branches that don't follow the
development way of working, **safely**. Repo-agnostic: it acts on whatever
repo it is run from, so the same script can be copied into the sibling
ITBisa repos.

Deletes (on remote `origin`):
  • AI-named branches — name contains `claude` / `chatgpt` / `codex`.
  • Branches still carrying an AI-authored commit not on `main`
    (e.g. `Claude <noreply@anthropic.com>`).
  • Branches already merged into `main` (content is preserved in `main`).

NEVER touches `main` or `bot-state`. Unmerged, non-AI branches are left
alone (they may hold work not yet in `main`) and only listed for review.

Default is a DRY RUN (prints, deletes nothing). To actually delete:

    python scripts/cleanup_branches.py --execute

Requires push access to `origin` with branch-delete permission.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

PROTECTED = {"main", "bot-state"}
AI_NAME_TOKENS = ("claude", "chatgpt", "codex")
# Author email/name fragments that indicate an AI-authored commit.
AI_AUTHOR_RE = r"anthropic\.com|chatgpt|codex"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def remote_branches() -> list[str]:
    out = _git("branch", "-r", "--format=%(refname:short)").stdout
    names: set[str] = set()
    for line in out.splitlines():
        b = line.strip()
        if not b or "->" in b:
            continue
        if b.startswith("origin/"):
            b = b[len("origin/"):]
        if b in PROTECTED:
            continue
        names.add(b)
    return sorted(names)


def _is_merged(branch: str) -> bool:
    return _git("merge-base", "--is-ancestor", f"origin/{branch}", "origin/main").returncode == 0


def _has_ai_commit(branch: str) -> bool:
    out = _git(
        "log", f"origin/main..origin/{branch}",
        "--perl-regexp", f"--author={AI_AUTHOR_RE}", "--format=%H",
    ).stdout.strip()
    return bool(out)


def classify(branch: str) -> str | None:
    """Return a reason this branch should be deleted, or None to keep it."""
    if any(tok in branch.lower() for tok in AI_NAME_TOKENS):
        return "ai-named"
    if _is_merged(branch):
        return "merged"
    if _has_ai_commit(branch):
        return "ai-commit"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete non-standard / stale branches on origin.")
    parser.add_argument("--execute", action="store_true",
                        help="actually delete (default: dry run, prints only)")
    args = parser.parse_args()

    _git("fetch", "--prune", "origin")

    branches = remote_branches()
    picks = {b: reason for b in branches if (reason := classify(b))}
    keep = [b for b in branches if b not in picks]

    if not picks:
        print("Clean — nothing to delete.")
    else:
        print(f"Branches to delete ({len(picks)}):")
        for b in sorted(picks):
            print(f"  - {b}  [{picks[b]}]")

    if keep:
        print(f"\nLeft alone — unmerged, non-AI ({len(keep)}); review manually if you want them gone:")
        for b in sorted(keep):
            print(f"  · {b}")

    if not picks:
        return 0
    if not args.execute:
        print("\nDRY RUN — nothing deleted. Re-run with --execute to delete.")
        return 0

    print()
    failed = 0
    for b in sorted(picks):
        result = _git("push", "origin", "--delete", b)
        if result.returncode == 0:
            print(f"deleted {b}")
        else:
            tail = (result.stderr.strip().splitlines() or [""])[-1]
            print(f"FAILED  {b}: {tail}")
            failed += 1
    print(f"\nDone. {len(picks) - failed} deleted, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
