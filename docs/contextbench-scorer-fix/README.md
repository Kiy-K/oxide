# ContextBench scorer: container-absolute gold paths

**Accepted fix; scoring only.**

- **Retrieval outputs did not change.** The fix touches gold-path handling
  in the scorer, never retrieval; every re-scored prediction was retrieved
  once and scored by both scorers from the same item list.
- **What changed:** exactly one of the 21 pinned instances
  (`Multi-SWE-Bench…8d780f70`, darkreader) had its **symbol, span and line**
  metrics mis-scored (symbol/span coverage a vacuous 1.0, line coverage 0);
  **file** metrics were always correct. In the current-`main` re-run, 4 of 84
  records change — that instance × {lexical, vec, hybrid, budgeted} (§4).
- **Decisions unchanged:** the frozen RRF K=60 / 0.6–0.4 configuration and
  every earlier reranker disposition (RRF K=10, confidence-aware reranker,
  flat structural bonus, and `docs/reranker-eval`'s cross-encoder
  rejection, whose arms were all mis-scored identically on that one
  instance) stand; re-scored paired deltas keep their signs (§4).
- Older reports keep their original numbers with a correction pointer
  (`docs/ranking-fusion-eval/README.md` §7.4,
  `docs/term-coverage-eval/README.md`).

## 1. Root cause

Some ContextBench rows store gold paths container-absolute —
`/workspace/darkreader__darkreader__0.1/src/utils/url.ts` (2,843 of 11,229
gold entries in `full`; `verified` is a subset, 319 of 4,597; SWE-bench's
`/testbed/` form does not occur). ContextBench's own `Gold.files()` strips those prefixes, so
**file** coverage was always correct. `scripts/agent_eval/contextbench_run.py
::evaluate_task` built the line/span/symbol gold from the *raw*
`item["file"]` instead, so for such a row:

| granularity | effect | direction |
| --- | --- | --- |
| line | 366 gold lines keyed by a path no prediction can have → coverage **0** | deflated |
| span, symbol | gold byte spans / definitions looked up at a nonexistent path → gold **empty**, coverage a vacuous **1.0**, precision 0 | coverage inflated, precision deflated |
| file | unaffected | — |

The same raw-path pattern was copied into five other scripts' gold readers:
`term_coverage_eval.py` and `reranker_eval.py` (`gold_lines`, which drive
`relevant_items`/P@5/recall@5), `docs/semantic-quality-eval/scripts/
cb_variants.py`, and `tierb_agent_run.py`/`tierb_solver.py` (gold-file
sets for "gold files utilized").

Within the 21-instance pin (`eval-agent/results/tier_a_instances.txt`,
identical to `docs/ranking-fusion-eval`'s ContextBench pin) exactly one
instance has prefixed gold. Across `verified`: TypeScript 14, Go 9, C 8,
Java 7, JavaScript 7, Rust 6, C++ 5 instances.

## 2. Fix

`contextbench_run.normalize_gold_path` strips exactly `/workspace/<one
segment>/` and `/testbed/` — nothing else. It deliberately does not reuse
upstream's `_normalize_rel_path`, whose `lstrip("./")` turns
`.github/…`/`.goreleaser.yml` (28 gold entries in `full`) into wrong
paths. Any other absolute path is left as is, so it still matches nothing
rather than being guessed into the repo: one `full` row has
`/tmp/reproduce_test.sh`, and 68 entries (42 rows) name a file directly
under `/workspace/` with no repository segment (`/workspace/reproduce.cpp`,
scratch files outside the repo). Upstream maps those to `reproduce.cpp`, so
file and line gold key them differently — harmless unless the repository
root happens to hold a file of that name; pinned by a test case.
`evaluate_task` and the copies call it (including the timeout paths of
`tierb_agent_run.py` and `tierb_solver.py`); no other scoring logic changed. (Upstream's own `lstrip` quirk still affects *file* coverage for
dot-path gold — pre-existing, inside ContextBench's `Gold.files()`,
recorded here and not changed.)

## 3. Tests

`scripts/agent_eval/test_contextbench_run.py` (20 pass;
`eval-agent/.venv/bin/python -m pytest scripts/agent_eval/test_contextbench_run.py`
— `pytest` was added to the eval venv for this):

- `test_normalize_gold_path` — workspace and testbed prefixes stripped;
  relative, dot-directory, other-absolute, bare `/workspace` and empty
  paths unchanged.
- `test_evaluate_task_scores_workspace_gold_exactly_like_relative_gold` —
  a `/workspace/…` row scores identically to its relative twin with real
  line/span/symbol gold. With the helper stubbed back to the old identity
  it fails and reproduces the historical signature exactly: symbol and span
  `(coverage 1.0, gold_size 0)`, line `(0.0, 4)`, file 1.0.
- `test_evaluate_task_does_not_rescue_unknown_absolute_gold` — no fuzzy
  matching beyond the two prefixes.

## 4. Historical corrections

### Re-run of the pin at current `main` (`raw/pin21_rescore.jsonl`)

`scripts/rescore_pin.py`: every (instance, condition) retrieved **once**
with the shipped default embedder and scored by both scorers from that one
item list (84 records, 84 distinct `items_sha256`). **Exactly 4 records
differ — `8d780f70` × {lexical, vec, hybrid, budgeted}; file metrics are
identical in all 84.** Means over 21 (old → corrected; `=` unchanged):

| condition | symbol cov | symbol prec | span cov | span prec | line cov | line prec |
| --- | --- | --- | --- | --- | --- | --- |
| lexical | 0.622 → 0.586 | 0.037 → 0.048 | 0.618 → 0.585 | 0.029 → 0.037 | 0.555 → 0.568 | 0.027 → 0.035 |
| vec | 0.495 → 0.447 | = | 0.483 → 0.435 | = | = | = |
| hybrid | 0.771 → 0.748 | 0.051 → 0.068 | 0.784 → 0.757 | 0.048 → 0.055 | 0.713 → 0.731 | 0.044 → 0.051 |
| budgeted (`context`) | 0.528 → 0.480 | = | 0.566 → 0.519 | = | = | = |

`8d780f70` itself, corrected: hybrid symbol 0.53 / line 0.39, lexical
0.24 / 0.28, vec and budgeted 0 / 0 (previously 1.0 / 0 everywhere).

### `docs/ranking-fusion-eval/results/cb-results.md` — exact re-score

Re-scored from the committed candidate dump with `cb_score.py` unchanged.
With the old scorer the output reproduces the committed table **exactly**
(`raw/ranking-fusion-cb-old.md`); corrected: `raw/ranking-fusion-cb-new.md`.
Every variant's symbol/span coverage falls; where the variant retrieves
any of `8d780f70`'s gold, line coverage and the precisions rise
("semantic only" retrieves none, so only its coverages change). E.g.
production K=60: symbol 0.771 → 0.748, symbol@5 0.670 → 0.631, span
0.784 → 0.757, line 0.713 → 0.731. Paired symbol-coverage deltas vs K=60
move by ≤ 0.025, keep their signs, and each changed variant gains one loss
(e.g. K=60 + rerank 2/4 → 2/5); "lexical only" symbol coverage's 95% CI now
excludes 0 (−0.162 [−0.341, −0.005]). The same stale absolute values are
quoted in `docs/ranking-fusion-eval/README.md` §7.4 (its table: K=60 symbol
0.771 / symbol@5 0.670, and the other variants' symbol columns), which never
names the instance; the K=10 delta quoted in §7.4/§7.6 is unchanged. **The frozen K=60 decision and the
rejected rerankers are unaffected.**

### Affected but not recomputable here (predictions not stored, or the arm needs retired code/servers)

| file | recorded for `8d780f70` | aggregate it enters |
| --- | --- | --- |
| `docs/reranker-eval/results/results.jsonl` | all 5 arms: symbol (1.0, gold 0), line (0.0, gold 366), `relevant_items` 0 | 21-instance means per arm; the bias is identical across arms, so arm-vs-baseline deltas are unaffected in expectation, but this instance contributed no signal |
| `eval-agent/results/qwen3_llamacpp_repro/cb_results.jsonl` | all 4 conditions: symbol/span (1.0, gold 0), line (0.0, gold 366) | 21-instance means (needs the qwen3 llama.cpp server) |
| `docs/term-coverage-eval/results/results*.jsonl` | every alpha: P@5 / recall@5 / MRR / nDCG 0, `gold_in_context` false | its README (line 246) lists it as "gold never retrieved at all" — that is the scoring artifact: corrected, lexical and hybrid retrieval do reach its gold at current `main` |

Unaffected: file-level-only sets that go through `Gold.files()`
(`eval-agent/results/{arctic_phase2,primary_cap,minilm_rerun}`,
`docs/cpu-embedding-survey`), `docs/coderankembed-eval` (own recall@k),
`docs/laya-reranking-eval` (never reached its ContextBench stage; the
ContextBench files there are inputs and timings), and
`docs/scip-rust-eval` (normalized its own gold).

Old results are left in place; this document is the correction record.
