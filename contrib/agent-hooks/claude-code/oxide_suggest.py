#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: suggest `oxide query` for discovery-shaped
prompts. Opt-in, local-first, non-blocking.

Contract (code.claude.com/docs/en/hooks, verified 2026-09-20):
  - Reads one JSON object from stdin: {session_id, transcript_path, cwd,
    permission_mode, hook_event_name, prompt}.
  - Exit 0 always. This hook never sets "decision": "block" and never uses a
    non-zero exit -- it only ever adds or withholds `additionalContext`, so it
    cannot block the prompt, deny a tool, or otherwise change what Claude is
    allowed to do. A parse error or unexpected input fails open (silent,
    exit 0), never fails closed.
  - additionalContext is the *exact* wording from docs/agent-usage-policy.md's
    "Recommended AGENTS.md snippet" (the phase-2.3-evidenced E1 variant) --
    restated, not reworded, per this repo's own rule that every OXIDE-facing
    surface must restate that policy document rather than fork it.
  - No network, no daemon, no LSP: one filesystem check for an existing
    `.oxide/index.db` and a handful of regexes over the prompt text.
"""
import json
import os
import re
import sys
from pathlib import Path

OXIDE_SNIPPET = (
    "For unfamiliar repository work where the implementation path is not "
    "already known, use `oxide query` before broad grep/read exploration. "
    "Use `oxide search` for focused follow-up discovery. For exact "
    "known-file or literal tasks, use normal tools directly. Read source "
    "before editing."
)

# Positive signal: the prompt reads like Bucket-A/B discovery work in
# docs/evals/phase-3.1/tasks.md's taxonomy -- a behavior/symptom description
# or "where/what" question with no file already named.
DISCOVERY_PATTERNS = [
    r"\bfind (where|the|that|this)\b",
    r"\bwhere (is|are|does|do)\b.{0,70}\b(handled|implemented|decided|computed|checked|located|lives?)\b",
    r"\bnobody (remembers|knows) where\b",
    r"\bsomewhere in this repo\b",
    r"\bthere'?s a report\b",
    r"\b(some )?users? (report|complain)\b",
    r"\bdoesn'?t seem to\b.{0,40}\bthe way\b",
    r"\bidentify (the|where)\b",
    r"\bwhat (calls|touches|uses|imports)\b",
    r"\bfind (every|all) (other )?(the )?(files?|places?)\b",
    r"\blocalize\b",
    r"\bfigure out where\b",
    r"\bwhich (file|function|module)\b.{0,30}\bhandles?\b",
]

# Negative signal: an explicit file path plus a small, targeted edit verb --
# Bucket-C's shape ("In `path/to/file.py`, rename X to Y"). This must win
# over a positive match: it's a stronger, more specific signal that OXIDE's
# own policy says to skip.
EXPLICIT_TARGET_RE = re.compile(
    r"[`'\"][^\s`'\"]+\.\w{1,5}[`'\"]|\bin\s+[\w/.\-]+\.\w{1,5}\b", re.IGNORECASE
)
TINY_EDIT_VERB_RE = re.compile(
    r"\b(rename|add a|add the|fix a typo|one-line|only touch|docstring|"
    r"add a comment|add a line)\b",
    re.IGNORECASE,
)

MIN_PROMPT_LEN = 25
INDEX_SEARCH_DEPTH = 4  # cwd + up to N parents; stops early at a .git boundary


def find_index_root(start: Path):
    cur = start
    for _ in range(INDEX_SEARCH_DEPTH + 1):
        if (cur / ".oxide" / "index.db").exists():
            return cur
        if (cur / ".git").exists():
            return None  # repo boundary reached without finding an index
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def state_path(session_id: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return base / "oxide" / "hook-seen" / f"{safe}.flag"


def already_suggested(session_id: str) -> bool:
    return state_path(session_id).exists()


def mark_suggested(session_id: str) -> None:
    flag = state_path(session_id)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()


def looks_like_discovery(prompt: str) -> bool:
    if len(prompt) < MIN_PROMPT_LEN:
        return False
    if EXPLICIT_TARGET_RE.search(prompt) and TINY_EDIT_VERB_RE.search(prompt):
        return False
    return any(re.search(p, prompt, re.IGNORECASE) for p in DISCOVERY_PATTERNS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt", "")
        cwd = payload.get("cwd", "")
        session_id = payload.get("session_id", "")
    except Exception:
        return 0  # fail open: never block a prompt over a parsing error

    if not prompt or not cwd:
        return 0

    if already_suggested(session_id):
        return 0

    if find_index_root(Path(cwd)) is None:
        return 0  # no local index here; suggesting `oxide query` would be a false positive

    if not looks_like_discovery(prompt):
        return 0

    mark_suggested(session_id)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": OXIDE_SNIPPET,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
