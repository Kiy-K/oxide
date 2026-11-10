# Blast radius in search and context results (design, 2026-11-11)

Status: predeclared before measurement. The gate below was fixed before any candidate
number existed. Results go to `docs/decisions.md`; the gate is not edited afterwards.

Verdict: REJECT (2026-11-11). G3 latency failed, so the code is reverted to HEAD. Numbers and cause: `docs/decisions.md`.

## Problem

`oxide_search` mode `impact` (2026-11-10) asks the agent to know it needs a second call.
Agents that find an entry point and stop never learn what depends on it. Impact should
arrive with the search or context result, bounded, not as a separate mode.

## Design

- `Repository.search` and `Repository.build_context` attach `blast`: one `BlastRoot` per
  entry point, for the first `BLAST_ROOTS = 3` entries in result order.
- Each `BlastRoot` reuses the upstream walk behind `Repository.impact`
  (`navigation.walk`, the same proven relations and bounds), with `BLAST_DEPTH = 2` hops
  and `BLAST_LIMIT = 20` entities. It reports: `key`, `dependents` (reached), `tests`
  (reached `test` entities), `files` (sorted distinct paths, at most `BLAST_FILES = 8`),
  `files_omitted`, `unresolved` (same-name call sites OXIDE could not resolve), and
  `stopped_by`.
- The blast radius is diagnostic: it is outside the context budget. Ranking, routing and
  the packed payload do not change.
- MCP: `oxide_search` drops `mode="impact"` and its `key`, `direction`, `depth` arguments.
  Search results carry `blast` in their structured and text payloads. Context text gets
  one `# blast` head line per root, so text-reading clients see it.
- CLI: `oxide impact` is unchanged and remains the detailed exploration tool. `oxide
  search` and `oxide context` print the same blast summary.
- Retained only if the gate passes. Otherwise the code is reverted and this document and
  the verdict stay as the record.

## Measurement scope

- Dev split of ContextBench-py only: 15 tasks over `fixture:py_repo`, `seaborn`, `flask`
  and six `requests` snapshots. The held-out split (`pydata/xarray`, `pytest-dev/pytest`)
  is not opened for this change.
- Baseline is HEAD `6967303`, extracted with `git archive` to `/tmp/oxide-head`, and run
  with the same interpreter and `PYTHONPATH`.

## Predeclared gate (all must pass to PROMOTE)

G1 navigation usefulness. Per dev task with verified gold, `bundle_recall` is the share of
gold files in the 1024-unit bundle. `with_blast_recall` adds the files surfaced by the
blast radius (as the API reports them). Macro mean over tasks: `with_blast_recall −
bundle_recall ≥ +0.02`, and at least one gold file surfaced by blast and absent from the
bundle across the dev split.

G2 payload unchanged. For every dev task, `payload@1024` hashes identical to HEAD, and the
dev contextbench scores (all budgets, verified and unverified) equal HEAD exactly.

G3 latency. `scripts/bench.py` with 5 iterations and 20 warm repeats, under
`systemd-run --user --scope -p CPUQuota=200% -p MemoryMax=3G`, on `fixtures/py_repo` and
`psf__requests@22623bd8c265`. Median `search_cold`, `search_warm`, `context_cold` and
`context_warm` may each rise at most 15% over HEAD on each corpus.

G4 token overhead. Mean MCP text bytes over the dev split (search at default limit,
context at 1024 units). Search text may rise at most 20%; context text at most 10%.
Structured bytes are reported, not gated. The chars/4 figure is an estimate; no tokenizer
is installed.

## Verdict rule

PROMOTE only if G1 through G4 all pass. Any failure: REJECT, revert the code, record the
numbers and the failing gate in `docs/decisions.md`. Held-out results are not part of
the decision and are not produced for it.
