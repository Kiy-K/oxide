# Phase 4.2 protocol — TypeSafe AI experiment

Separate from phase-4.1 (hook-based activation). Governing question: does
TypeSafe's System One judgment API (Noul primitive) improve on OXIDE's
existing heuristics on two *distinct* false-positive-prone surfaces, without
touching production code, without transmitting sensitive repository
content, and staying strictly outside OXIDE's production retrieval path?

## 0. What TypeSafe actually is (read before designing anything)

TypeSafe is not a bug-finder or a retrieval engine on its own. Per
`docs.typesafe.ai` (fetched 2026-09-20): it is a generic judgment API —
`POST https://api.typesafe.ai/v1/systemone`, given a `state` (the content to
evaluate) and a map of typed `questions` (Noul = yes/no probability, Choice =
pick one option, Score = graded rating), returns typed `answers`. It supplies
"programmable common sense where ordinary code needs semantic understanding"
— code still owns the workflow. This matters: nothing about TypeSafe is
pre-built for "find bugs" or "rerank retrieval" — those are *patterns* you
compose from Noul/Choice/Score, per its own use-case map and cookbooks
(`rerank_typesafe.md`, `hierarchical_classification.md`). This experiment
uses only the Noul primitive, on both axes, since both reduce to yes/no
questions ("does this need discovery", "is this candidate relevant").

Pricing: `jev-1.13.0` (aliased `jev-latest`), $0.042 per million input tokens,
output free. Negligible for this experiment's scale (both axes combined:
~32k input tokens, ≈ $0.0014 total).

## 1. Scoping "code-review false positives" — a decision made with the user

The task said "distinguish activation false positives from code-review false
positives; do not assume they share a cause" without naming which existing
surface the code-review axis meant. Two candidates were investigated before
asking:

- The real, currently-active hooks in this environment: a project-level
  `PostToolUse` rustfmt formatter (`.claude/settings.json`), a user-level
  `UserPromptSubmit` hook running `codegraph prompt-hook` on every prompt,
  and (unrelated to OXIDE) a third-party ECC-plugin "GateGuard" fact-forcing
  gate observed firing repeatedly this session on ordinary file writes.
- OXIDE's own `oxide review` command (`AGENTS.md`'s WORKFLOWS command,
  "Build context for a git diff") — a genuinely separate code path and
  heuristic from `query`/`search`'s hybrid retrieval, per its own
  implementation (`structural_relations`-derived `related[]` list with
  `child←`/`sibling←`/`test←`/`uses←`/`imported-definition←` reasons, not
  BM25+cosine fusion).

**User's decision: `oxide review`'s context relevance** — the false positive
is an item in that context pack that isn't actually relevant to reviewing
the diff. This keeps the experiment inside OXIDE's own surface (reproducible,
no third-party plugin internals to reverse-engineer) and gives a genuinely
different heuristic/code-path from phase-4.1's activation hook, satisfying
"do not assume they share a cause" with real, not assumed, mechanical
separation.

## 2. Two axes, two datasets, one shared TypeSafe client

`raw/typesafe_client.py`: a ~70-line stdlib-only (`urllib`, no new
dependency) HTTP client. Reads `TYPESAFE_API_KEY` from the repo's `.env`
(gitignored, confirmed via `git ls-files | grep .env` returning nothing) and
never logs, prints, or returns the key itself — only call results. Retries
on `429`/`529` with exponential backoff per TypeSafe's own documented
guidance.

### Axis 1 — activation false positives

Reuses phase-4.1's exact 34-prompt held-out set
(`docs/evals/phase-4.1/raw/labeled_prompts.jsonl`) verbatim — the task's own
"use identical held-out cases" instruction, satisfied literally rather than
by re-deriving a new set that could quietly differ in difficulty. One Noul
question per prompt: "does this task require exploratory discovery... as
opposed to a task that already names the exact file/symbol?" Sends only the
prompt text — itself a synthetic eval sentence written for phase-4.1, never
OXIDE's own source.

### Axis 2 — code-review false positives (`oxide review` relevance)

1. `raw/setup_review_fixtures.py` builds two tiny git repos from
   **already-existing eval-agent fixtures** (`eval-agent/tasks/py_bug_retry`,
   `ts_bug_store` — not OXIDE `src/`), each with a real bug-fix commit (the
   fixtures' own pre-marked `# BUG:`/`// BUG:` lines, fixed to match their
   existing test assertions) and a cosmetic/clean-control commit (a
   docstring added to an unrelated method, no behavior change) — the "clean
   control" the task asked for, mirroring phase-4.1's Bucket-C design.
   Indexes each state (`OXIDE_EMBED_NATIVE=hashed`, matching the benchmark
   gate's deterministic embedder) and runs the real
   `oxide review --diff <A>..<B> --json` on both diffs per repo (4 diffs
   total). Output: `raw/review_fixture_output.json`.
2. `raw/review_relevance_labels.json`: every symbol OXIDE's `related[]` list
   surfaced for each of the 4 diffs (39 (diff, symbol) pairs total),
   manually labeled relevant/irrelevant by reading the actual fixture code,
   each with a written reason — not a rubber-stamp of OXIDE's own output.
   OXIDE's own "relevant" prediction is read off `related[].score`: every
   score in the real output falls cleanly into ~2.0/~1.0 (a structural
   reason present: `child←`/`sibling←`/`test←`/`uses←`/
   `imported-definition←`) or ~0.01-0.02 (semantic-neighbor only, ~0
   semantic score) — a genuine bimodal split in the data, so `>=1.0` is a
   measured cutoff, not an arbitrary one chosen to flatter either system.
3. `raw/axis2_review_relevance_eval.py`: for the same 39 pairs, asks
   TypeSafe one Noul question per pair — "would a competent reviewer need to
   look at this candidate to verify the change is correct and complete?" —
   giving it the diff description + changed-symbol signatures + the single
   candidate's signature (not its full body, and not OXIDE's own
   caller/callee graph). Scores both OXIDE's and TypeSafe's predictions
   against the same manual labels.

**Caveat stated plainly**: TypeSafe was given *less* information per
candidate than OXIDE's own structural-relations index has (no call-graph
edges, no body text) -- its measured ceiling here is a lower bound on what a
richer `state` could achieve, not TypeSafe's best possible performance. This
is disclosed, not smoothed over, in `results.md`.

## 3. Safety constraints honored throughout

- No OXIDE `src/` file read or sent anywhere; both axes' `state` payloads
  are synthetic eval prompts (axis 1) or the two small eval-agent fixtures
  already used across this repo's own eval history (axis 2), never
  proprietary retrieval-engine source.
- The API key is read once from `.env`, held only in a module-level
  variable, and never appears in any printed output, log, or committed file
  — every script in `raw/` was written to that constraint before its first
  network call, not patched afterward.
- No production code modified. Zero `src/` diff, confirmed via
  `git diff --stat -- src/` (empty) at the end of this phase.
- Nothing committed or pushed. Everything in this phase sits in the working
  tree awaiting the user's explicit approval, exactly as instructed.

## 4. Verification

`git diff --stat -- src/` -- empty, confirming this phase touched no
production code and therefore cannot have changed retrieval behavior or any
existing correctness gate. `mise run verify`'s full byte-identical pass was
already confirmed once this session (phase-4.1, same zero-`src/`-diff
guarantee); not re-run a second time for an experiment that touches even
less of the repo (no `contrib/` hook code, only `docs/evals/`).
