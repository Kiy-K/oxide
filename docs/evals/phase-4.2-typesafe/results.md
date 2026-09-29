# Phase 4.2 results — TypeSafe AI experiment

## Axis 1 — activation false positives (n=34, identical held-out set as phase-4.1)

| | precision | recall | false-positive rate | mean latency | cost |
|---|---|---|---|---|---|
| **Regex heuristic** (`oxide_suggest.py`, phase-4.1) | 1.00 | 0.80 | 0.00 | ~0.04s | $0 (no API) |
| **TypeSafe Noul** (`jev-1.13.0`) | **1.00** | **1.00** | **0.00** | 0.82s | $0.00056 (13,314 input tokens) |

TypeSafe correctly fired on all four prompts the regex heuristic missed
(`A6`, `B2`, `B3`, `B4` — the exact false negatives phase-4.1's own
`results.md` disclosed as "real regex-coverage gaps, not implementation
bugs"), while still correctly staying silent on all 14 Bucket-C prompts,
including the adversarial `C13` ("Find the line in `src/main.rs` that
prints the banner...") that was specifically designed to trip a
keyword-matching approach — TypeSafe scored it `noul=0.17`, correctly below
threshold, showing it isn't just keying off the word "find."

**Trade-off, not a free win**: TypeSafe's mean latency (0.82s) is ~20x the
regex heuristic's (~0.04s), and it requires a network round-trip with a
private API key on every prompt, where the regex heuristic requires neither.
Claude Code's `UserPromptSubmit` hook has a 30s default timeout (5s in this
repo's own `contrib/agent-hooks/claude-code/settings.snippet.json`) — 0.82s
fits comfortably, but a hook this slow is no longer the "no LSP, daemon, or
remote dependency in the runtime path" design phase-4.1 built to. Cost is
negligible at this scale ($0.00056 for 34 calls) but is real, ongoing,
per-prompt spend that a zero-cost local heuristic doesn't carry.

## Axis 2 — code-review false positives (n=39 (diff, symbol) pairs, `oxide review` relevance)

| | precision | recall | false-positive rate | mean latency | cost |
|---|---|---|---|---|---|
| **`oxide review`** (existing, `related[].score >= 1.0`) | 0.37 | 0.70 | 0.41 | (local, no API) | $0 |
| **TypeSafe Noul** (`jev-1.13.0`) | **0.70** | 0.70 | **0.10** | 0.79s | $0.00079 (18,822 input tokens) |

Both systems tie on recall (0.70, 7/10 genuinely relevant symbols found by
each). TypeSafe nearly doubles precision (0.70 vs 0.37) and cuts the
false-positive rate by 4x (0.10 vs 0.41) on the same 39 manually labeled
pairs.

**Where `oxide review`'s false positives come from, traced to a specific
mechanism, not just counted**: on the two *clean* (cosmetic, no genuine bug)
diffs, `oxide review` produced 9 of its 12 total false positives:

- `py_retry_clean` (docstring only): flagged `HttpClient._fetch` and
  `HttpClient.__init__` relevant purely via `sibling←HttpClient.request_json`
  — a docstring on one method pulls in every sibling method regardless of
  whether the docstring says anything about them.
- `ts_store_clean` (docstring only): flagged **7 of 9** candidates relevant,
  every one of them via `imported-definition←AuditLog` — because
  `audit.ts` imports from `versioned_store.ts`, `oxide review` treats
  nearly that entire imported file's public surface as relevant to a
  one-line docstring change that touches none of it. This is the single
  largest source of false positives in the whole axis-2 dataset (7/12).

TypeSafe produced 0 false positives on `ts_store_clean` and only 1 false
positive total across both clean diffs (on `py_retry_clean`, scored just
above threshold) — see `axis2_results.json` for the full per-pair table.
TypeSafe's judgments were sensitive to the *directional* information in the
diff description itself (a docstring diff reads as cosmetic) in a way
`oxide review`'s structural-reason mechanism is blind to by construction —
`child←`/`sibling←`/`imported-definition←` fire on structural proximity
alone, with no notion of "but did the change actually touch anything this
candidate depends on."

**Both systems share the same core false negative**
(`AuditLog`/`AuditLog.record` never scored relevant by either system for the
`VersionedStore.set` fix, despite `AuditLog.record` being the direct real
caller of the fixed method) — evidence this specific gap is a genuine
information-availability problem, not a heuristic-specific defect: neither
`oxide review`'s file-scoped structural expansion (per `AGENTS.md`'s own
documented "scope to seed files" limitation) nor TypeSafe's per-candidate
judgment (which was never told `AuditLog.record` calls `store.set()` — see
caveat below) had the cross-file call-graph edge needed to catch it.

**Caveat, disclosed rather than smoothed over**: TypeSafe was given
*strictly less information per candidate* than `oxide review`'s own index
has — a bare signature and file path, no function body, no caller/callee
edges. Its measured precision/recall here is a **lower bound** on what a
richer `state` (e.g., including the candidate's body, or its known
callers) could achieve, per TypeSafe's own documented guidance ("Give each
question enough relevant state to answer"). This experiment did not test
that richer configuration — doing so is listed in `recommendation.md` as
follow-up work, not claimed here as already measured.

## Independent verification of the ground-truth labels

The 39 manual labels (`raw/review_relevance_labels.json`) were not scored
against themselves in one pass and left unchecked: each carries its own
written reason, checked a second time while writing this report against the
actual fixture source (re-read, not re-guessed) for every label flagged
"borderline" during authoring (`AuditLog.record` in `ts_store_clean`,
labeled relevant despite `oxide review` attributing the diff to the
enclosing class rather than the method itself — confirmed correct on
re-reading: the docstring textually documents that exact method). No label
was changed as a result of this second pass, which is itself a data point
(the first-pass labels held up), not assumed a priori.

## Production code / retrieval behavior

`git diff --stat -- src/` — empty. This phase changed no OXIDE production
code and therefore cannot have altered retrieval behavior or any existing
correctness gate.

## Credential and data-handling compliance

- `TYPESAFE_API_KEY` was read once from `.env` and never printed, logged, or
  written to any file this phase produced (spot-checked: `grep -r` for the
  key's own value across every file in `docs/evals/phase-4.2-typesafe/`
  returns nothing).
- No OXIDE `src/` file was ever included in a TypeSafe `state` payload —
  only phase-4.1's own synthetic eval prompts (axis 1) and the two
  already-existing, non-proprietary `eval-agent/tasks/*` fixtures (axis 2).
- Nothing in this phase was committed or pushed.
