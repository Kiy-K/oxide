# ch5 fresh held-out validation — preregistration

Written **before** any fresh task was generated or any ch5 output on fresh data
was seen. Its SHA-256 is recorded in `~/.cache/oxide-ch5-fresh/prereg.sha256`
with a timestamp before task generation starts. After that point, nothing in
this file changes except appended, clearly marked post-hoc notes.

- Research record: local `main` `6ec73b6` (docs only). `origin/main` is still
  `1abb3d7`, not pushed.
- Production baseline: `1abb3d7dd601aa63da06e5336b1ae8e93db75e1d`. `src/`,
  `examples/`, `tests/` and the Cargo files are identical at `6ec73b6`.
- Worktree: `/tmp/oxide-ch5-6ec73b6`. Scratch data lives in
  `~/.cache/oxide-ch5-fresh/`.

## 1. Frozen ch5 contract (Option A: the recorded implementation, verbatim)

The code is `docs/alloc-utilization-eval/research.patch`, applied unmodified.
The arm is `alloc_research` challenger 5. The contract:

1. Run the shipped allocator unchanged. The baseline pack is complete first.
2. Then revisit only candidates whose baseline omission reason is exactly
   `per-file diversity cap`.
3. Visit them in the allocator's own fill order. That is `kept` after the
   relevance floor, sorted by role (primary, then dependency, then test), then
   by score descending, then by symbol id.
4. A recovered candidate may push the pack past the 5-primary cap.
5. Test-role candidates are never recovered. The test cap is not relaxed.
6. Candidates omitted for any other reason are never reconsidered: primary cap,
   test cap, relevance floor, module or overlap subsumption, over budget, or
   never retrieved.
7. A file may hold at most 4 items in total (2 × `CONTEXT_MAX_ITEMS_PER_FILE`),
   counting baseline items.
8. Rendering is identical to the baseline: `render_snippet` with the 350-token
   per-item cap and shrink-to-fit halving, and the same token estimate plus the
   12-token overhead.
9. A candidate is admitted only if it fits the remaining 4096-token budget and
   its rendered snippet is non-empty.
10. Recovered items are appended after every baseline item, so the baseline pack
    is an unchanged prefix. They carry the reason
    `alloc-topup(per-file diversity cap)` and are removed from `omitted`.

Arms: **A** = challenger 0 (shipped), **B** = challenger 5. Mode: balanced (the
default). Budget: 4096. No other arm is run.

## 2. Fresh dataset construction (fixed before generation)

- **Method** (same as #30's held-out): `docs/ranking-fusion-eval/scripts/make_tasks.py`,
  unmodified.
  - It runs in `--worktree` mode on each repository's history: newest-first,
    non-merge commits, ≤4 changed source files, ≤8 files, a ≥6-word message,
    and no bump/changelog/release/typo/lint/docs/ci commits.
  - **Query** = the commit subject and body, with trailers, issue refs and URLs
    stripped.
  - **Gold** = the changed non-module symbols, from `oxide review --diff
    sha~1..sha` (structural span mapping), between 1 and 8 of them.
- **Parent corpora** (leakage control, same as #30's `parent.sh`): each task is
  re-indexed at `sha~1` with the default embedder. Gold is filtered to symbols
  that exist in the parent index, and tasks with no remaining gold are dropped.
- **Line gold**: edit-locus lines from
  `docs/alloc-utilization-eval/scripts/heldout_edit_gold.py`, unmodified.
  - **Primary analysis excludes** any task where no touched line falls inside a
    gold symbol, i.e. where the script used its all-touched-lines fallback
    (Codex finding 5 of the previous study). Those tasks are reported
    separately.
- **Repositories** (8 repos, 3 languages, at most 8 tasks each, target 64):
  - Python: httpx, requests, flask.
  - Rust: ripgrep, clap, rayon.
  - TypeScript: zod, axios.
  - Local clones are used as they are (no fetch). make_tasks scans the 900 most
    recent non-merge commits from each clone's HEAD.
- **Exclusions**: `~/.cache/oxide-ch5-fresh/used_shas.json` holds 357 commit
  SHAs or 12-character snapshot prefixes. They come from every task file in
  `docs/**/*.jsonl`, `~/.cache/oxide-intent-eval/tasks/`,
  `~/.cache/oxide-semantic-eval/tasks-*.jsonl` and the ContextBench snapshot
  directories. Let S be that set plus the parent of every used commit. A
  candidate commit c is excluded if c ∈ S or c~1 ∈ S, matched by 12-character
  prefix. Near-duplicates (same repo, same gold, query Jaccard ≥ 0.6) keep only
  the newest.
- **Binary**: the pristine `oxide` built from `1abb3d7` indexes every corpus
  (`~/.cache/oxide-alloc-eval/bin/oxide-pristine`). Default native embedder
  `arctic-embed-xs-q`, with `OXIDE_EMBED_*` unset.
- Task generation, parent indexing and gold extraction all finish, and the task
  file is hashed, **before** the first ch5 run on it. Nobody looks at ch5 output
  while building queries or gold.

## 3. Metrics

Per task:

- **Gold-line coverage**: delivered snippet lines ∩ edit-locus gold lines,
  divided by the gold lines.
- **Relevant tokens**: characters of the delivered gold lines ÷ 4.
- **Used tokens.**
- **Efficiency**: relevant tokens ÷ used tokens.
- Pack size, token utilization (used ÷ 4096) and file diversity (distinct
  files).

Per ch5-added item:

- Symbol, file, role and original omission reason.
- Original rank in fill order, and fused retrieval rank.
- Tokens added.
- Whether it crossed the 5-primary cap.
- Whether it contains gold.
- The task's coverage delta and efficiency delta.

Statistics use a paired bootstrap over tasks: 10,000 resamples, seed 0, 95 %
percentile intervals. The scoring code is
`docs/alloc-utilization-eval/scripts/score_alloc.py` plus a validation script
written before the fresh run.

## 4. Acceptance gates (all must pass for PRODUCTION CANDIDATE)

| gate | criterion |
|---|---|
| Q1 quality | mean Δ gold-line coverage (B − A) > 0, **and** the 95 % CI lower bound > 0 (primary analysis set) |
| E1 efficiency | relative efficiency change = (mean_task eff_B − mean_task eff_A) ÷ mean_task eff_A. The 95 % CI lower bound of this ratio (same resamples) must be ≥ −5 %. The point estimate alone is not enough. |
| R1 regressions | report improved / unchanged / regressed task counts by coverage. Every coverage regression, and every task with an efficiency drop, is inspected individually and explained. Any coverage regression that cannot be explained fails R1. |
| O1 latency | median per-request ch5 − baseline context time ≤ 5 % of median search time on the same tasks |
| O2 memory | peak RSS (VmHWM) increase ≤ 5 % on the three largest fresh corpora (separate processes, alternating) |
| P1 parity | the instrumented binary with ch5 disabled gives byte-identical `oxide query --json` to the pristine `1abb3d7` binary on every fresh task, and no `index.db` changes |
| P2 canonical | fixture gate `oxide eval --config fixtures/benchmark.json` unchanged (hybrid R@5 0.909, vector 0.818) |

- **Validity**: the evaluation is valid only with ≥ 50 primary-analysis tasks
  from ≥ 6 repositories, and no repository contributing > 20 % of them.
  Otherwise the disposition is INSUFFICIENT EVIDENCE.
- **Disposition**: PRODUCTION CANDIDATE if every gate passes. REJECT if any of
  Q1, E1, R1, O1, O2, P1 or P2 fails. INSUFFICIENT EVIDENCE only on the
  validity rule above.
- **Mechanism** (reported, not gated): is the gain same-file recovery or just
  more primaries? This is answered by splitting recovered items by role and by
  whether they crossed the primary cap, and by comparing their gold-hit rate
  with the baseline pack's items.

Old datasets (ContextBench, dev, BCD, CA and the #30 held-out set) are
historical context only, not acceptance evidence.
