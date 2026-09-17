# Git-aware context

OXIDE can fold the current diff, recent commit metadata, and bounded
historical co-change evidence into `oxide query`'s bounded context allocator
(`--git`) and into `oxide review`'s always-on diff view. This is not a new
retrieval backend — it is one more evidence source feeding `context.rs`'s
existing candidate pool, on the same terms as `--blast-radius`.

Commits: `a1397b7` (diff parsing fixes, `gitctx.rs`, bounded co-change),
`fe4bb6c` (wiring into `context.rs`/service/CLI/MCP), `8b9b296` (`review.rs`
enrichment + two bug fixes), `d8d861e` (edge-case tests). Plan:
https://plan.ref.tools/x2Hh4qFsaBHi7PYb.

## Architecture

```
retrieval anchors (hybrid search)
+ structural relations (RelationGraph, precomputed)
+ current git diff (gitctx::build_git_context)
+ bounded co-change history (same call)
        ↓
  context.rs's candidate pool (order_note), scored + deduped + budgeted
```

Two low-level layers, matching the two "live vs historical" git evidence
kinds:

- **`src/gitutil.rs`** — thin `git` subprocess wrappers: `diff_text`/
  `parse_unified` (unified diff → per-file added-line ranges, now handling
  pure-deletion hunks and file-deletion sections correctly), `recent_commits`/
  `recent_commits_in_range` (bounded `git log`), `co_change_raw` (two bounded
  calls per file — see below). No `git2` dependency; the existing `diff_files`
  code already took this approach for v0.1's review command.
- **`src/gitctx.rs`** — the domain layer both `context.rs` and `review.rs`
  call: `changed_symbols_for` (diff hunks → indexed symbols, shared so the
  mapping has exactly one implementation), `build_git_context` (assembles
  everything into a `GitContext` = non-symbol `GitEvidence` + symbol-level
  `changed_symbols`), and `co_change_for` (the ranking/noise-filtering logic
  below). Storage-agnostic: it takes a `symbols: &[Symbol]` slice the caller
  already loaded, never touches `IndexBackend` directly.

`context.rs`'s git block sits inside the existing `if !seeds.is_empty()`
scope, right after the `--blast-radius` block, and reuses the same
`RelationGraph`/symbol snapshot already loaded there — `--git` never triggers
a second corpus load or, when absent, any `git` subprocess call at all. Every
git-derived candidate enters the pool through the same `order_note(Candidate
{...})` path as every other evidence source, so it competes for the token
budget, per-file diversity cap, and relevance floor exactly like a structural
or blast-radius hit — there is no second allocator.

## Git evidence semantics

**Current diff** (`gitctx::changed_symbols_for`): symbols whose line range
overlaps an added-line range from `git diff --unified=0` against `HEAD`
(worktree vs HEAD covers staged and unstaged changes together, since `git
diff HEAD` already compares the full working tree — including the index —
against `HEAD`). A pure-deletion hunk (`+N,0`) is kept as a small window
around the new-side insertion point rather than dropped, so deleting a line
from inside a method still attributes to that method; a symbol deleted in
full has nothing left in the *current* file to attribute to, which is a
stated limitation (see below), not a bug. A whole-file deletion still appears
in `changed_files` (parsed from the `--- a/<path>` / `+++ /dev/null` pair)
even though it maps to no symbol.

**Recent commits** (`gitutil::recent_commits`/`recent_commits_in_range`):
subject line only, never the commit body — this is provenance metadata, not
a retrieval input, and the task explicitly rules out semantic commit-history
search. Bounded to `GIT_RECENT_COMMITS_LIMIT` (10), `--no-merges`, UTC
`Z`-suffixed timestamps (`TZ=UTC` + `--date=iso-strict-local`, string-swapped
to `Z` — no date crate needed for one field). For `oxide review --diff
<range>`, this is scoped to the diff's own range (`R..HEAD`, not "most
recent at HEAD") — what a reviewer actually wants to know is what happened
*in this diff*.

**Bounded co-change** (`gitctx::co_change_for`): for each of the first
`GIT_COCHANGE_MAX_TARGET_FILES` (5) changed files (sorted by path), `git log
--no-merges -n GIT_COCHANGE_COMMIT_WINDOW(50) --format=%H -- <file>` gets the
bounded list of commits that touched it, then one batched `git diff-tree
--stdin` call gets every one of those commits' *full* file lists at once —
one process spawn per target file for the SHA list, one more shared across
all of them for the file lists, never one process per commit. Two safeguards
against the "universal neighbor" failure mode a naive raw-count tally would
have:

- **Noisy-file stoplist** (`gitctx::is_noisy_cochange_file`): lockfiles
  (`Cargo.lock`, `package-lock.json`, `yarn.lock`, `poetry.lock`,
  `Pipfile.lock`, `Gemfile.lock`, `composer.lock`, `pnpm-lock.yaml`, `go.sum`)
  and changelogs (`CHANGELOG*`, `CHANGES*`) are excluded from co-change
  candidates entirely, since they change alongside nearly every commit that
  touches anything.
- **Coupling ratio, not raw count**: ranked by `strength = shared_commits /
  commits_touching_the_target_file` (within the bounded window) — a
  CodeScene-style temporal-coupling ratio, adapted to use only the target
  file's own commit count as the denominator since the *candidate* file's
  true total isn't cheaply knowable without a second bounded call per
  candidate. A commit touching more than `GIT_COCHANGE_MAX_FILES_PER_COMMIT`
  (20) files is dropped from the tally entirely (a mass reformat or
  dependency bump is noise, not signal), and pairs below
  `GIT_COCHANGE_MIN_SHARED` (2) shared commits are dropped as coincidence.
  Results are sorted `(strength desc, path asc)` before truncating to
  `GIT_COCHANGE_MAX_FANOUT` (5) — most real pairs co-change once or twice, so
  the tie-break is load-bearing for determinism, not cosmetic.

**Priority, lowest to highest**: co-change evidence is explicitly the
lowest-confidence tier. Score fractions (of the query's top seed score, the
same convention `--blast-radius` uses) are all below
`BLAST_RADIUS_SCORE_FRACTION` (0.35) and all above the relevance floor
(0.15 — otherwise they'd never survive a query that also has one strong
primary):

| Evidence | Fraction | Why |
|---|---|---|
| Changed symbol (the diff itself) | `GIT_CHANGED_SCORE_FRACTION` = 0.28 | A fact, one tier below structural expansion (0.4) and blast radius (0.35) |
| Caller/test of a changed symbol | `GIT_NEIGHBOR_SCORE_FRACTION` = 0.20 | One structural hop removed from that fact |
| Co-changed symbol | `GIT_COCHANGE_SCORE_FRACTION` (0.16) × the pair's own `strength` | A heuristic — deliberately the lowest, and further scaled down by confidence |

`callers_of()` (used for the middle row) is repo-wide, bare-name-matching,
and scope-resolution-free by construction (AGENTS.md) — its results are
filtered to the query's own seed-pool files before reaching the candidate
pool, the same bound the pre-existing structural-callers expansion a few
lines above it in `context.rs` already applies. See Review below for why
this needed fixing during development.

Every git-derived candidate's reason string is namespaced stable vocabulary
(matching the existing `blast-radius:`/`ast-grep-caller` convention):
`git-changed(+N)←file`, `git-caller-of-changed←qualified_name`,
`git-cochange(N commits)←file`.

**JSON/MCP exposure**: `ContextPack.git: Option<GitEvidence>` (query) and
`ReviewContext.{changed_files,changed_symbols,recent_commits,co_change}`
(review, always present) carry the non-symbol provenance —
`{range, changed_files, recent_commits, co_change}`. Symbol-level evidence
(changed symbols, their callers, co-changed symbols) is *not* duplicated
there; it flows through the ordinary `items`/`reasons` pipeline like every
other evidence source, so an agent reading `reasons` sees exactly why each
item is present regardless of source.

## Performance

`--git` off is byte-identical to the pre-feature pack — no snapshot load
beyond what other opt-in features already trigger, no `git` subprocess call,
no `git` key in the JSON at all (verified in
`tests/git_context_e2e.rs::absent_git_flag_leaves_query_json_byte_identical`
and manually diffed before/after). `oxide eval --config fixtures/benchmark.json`
is unaffected (git evidence never runs during eval): hybrid recall@5 0.909 /
precision@5 0.200, vector-only 0.818 / 0.182 — unchanged from
`README.md`'s committed table, since eval never sets `--git`.

Measured wall-clock (`target/release/oxide query ... --json`, hashed
embedder, 8 runs each, `nice -n 10`, on `fixtures/py_repo`/`fixtures/ts_repo`
copied to scratch with a real one-commit git history and one uncommitted
edit):

| Repo | `--git` off | `--git` on | Overhead |
|---|---|---|---|
| py_repo (8 files) | ~8 ms | ~16 ms | ~8 ms |
| ts_repo (8 files) | ~6 ms | ~16 ms | ~10 ms |

The overhead is dominated by `git` process spawns (one `diff`, one `log` for
commits, one `log` + one `diff-tree` per co-change target file — bounded at
5 target files regardless of diff size) rather than anything scaling with
repository size; it does not grow with corpus size the way a retrieval-path
regression would; the same 5 changed-file cap keeps it bounded even for a
very large diff.

## Limitations (by design, not oversights)

- **Diff-to-symbol mapping uses the last-*indexed* state, not live disk.**
  `changed_symbols_for` overlaps a *live* `git diff`'s line ranges against
  symbol positions from whatever was last indexed. If a file has been edited
  since the last `oxide index` in a way that shifts line numbers, attribution
  can be imprecise or miss entirely — this degrades gracefully (a hunk that
  overlaps nothing just maps to nothing, never a crash) rather than erroring,
  but it is not corrected automatically. This is not unique to git evidence —
  every OXIDE feature answers against the last-indexed state — but git
  evidence is the one feature that explicitly correlates against *live* diff
  line numbers, so staleness here is more visible than elsewhere.
  `tests/git_context_e2e.rs::git_evidence_reflects_the_currently_indexed_working_tree_state`
  pins the correct (reindexed) case.
- **A fully deleted symbol is invisible.** Only the current-state file is
  parsed for symbols; a function deleted in its entirety has nothing left in
  the current tree to attribute the deletion to. Naming it would need
  parsing the old blob's structure too — out of scope for v1.
- **Co-change strength uses only the target file's own commit count**, not
  `min(commits_A, commits_B)` the way CodeScene's full ratio does — the
  candidate side's true total isn't cheaply knowable without one more bounded
  `git log` call per candidate, which would multiply subprocess count by
  `GIT_COCHANGE_MAX_FANOUT`. The noisy-file stoplist and the
  files-per-commit cap cover the most damaging case (a universal-neighbor
  file) more cheaply.
- **No index-time precompute.** Everything here is computed live per query,
  bounded by small fixed windows. This was a deliberate v1 choice (task
  instruction: "cache/precompute only if benchmarks justify it") — the
  measured overhead above didn't.
- **Cross-repo / semantic commit-history search is out of scope**, per the
  task's own phasing — this is diff + commit metadata + bounded co-change
  only.
- Untracked files are excluded from all git-aware evidence — `git diff HEAD`
  never sees a file that was never `git add`ed.
- A pure rename with no content change produces no diff hunk, so the
  renamed file is invisible to `--git`'s evidence.
- Git-enabled retrieval quality has not been validated by any agent-outcome
  benchmark — only correctness/plumbing tests exist today.

## Testing

- `src/gitutil.rs`, `src/gitctx.rs`: unit tests against synthetic diffs and
  small real git repos (deletion-hunk attribution, the `+++ /dev/null`
  `cur`-reset bug, noisy-file recognition, determinism).
- `tests/review_e2e.rs`: `recent_commits` includes the commit that made a
  reviewed change (this caught a real `git log`-vs-`git diff` range-direction
  bug during development — see `8b9b296`).
- `tests/git_context_e2e.rs`: staged/unstaged, rename, partial/whole-file
  deletion, merge commit, genuinely shallow clone, detached HEAD, non-git
  directory (both `query --git` and `review`), determinism across repeated
  runs, byte-identical output with `--git` absent, and incremental-reindex
  interaction.
- `tests/mcp_e2e.rs`: the `query` MCP tool schema/allowlist assertions
  include `git`.

## Review

A first Codex CLI review attempt failed to start (a pre-existing, unrelated
local environment misconfiguration); a self-audit was run in its place and
missed the most important finding below — worth stating plainly, since it's
exactly the gap a second model is for. Once the environment was fixed, a
real Codex review ran and found two issues serious enough to act on:

- **BLOCKER — unscoped `callers_of()` in the git block.** `context.rs`'s
  git-changed-symbol caller lookup called `graph.callers_of(&cs.symbol.name)`
  with only an item-count cap, not the file-scope filter AGENTS.md's
  invariant requires for every `callers_of`/`implementors_of` consumer
  (`callers_of` matches by bare name with no scope resolution and is
  repo-wide by construction — the one sanctioned exception is
  `blast_radius.rs`, which isn't this). A changed symbol with a common bare
  name (`save`, `run`, `helper`) could pull an unrelated same-named symbol's
  caller from anywhere in the repo. Fixed: scoped to the query's own seed
  pool files, the same bound the pre-existing structural-callers block a few
  lines above already uses. Pinned by
  `git_caller_expansion_is_scoped_to_the_seed_pool_not_repo_wide` — which
  needed a corpus of ~25 symbols to mean anything, since a tiny corpus makes
  every symbol a weak "primary" seed regardless of the fix (confirmed
  empirically while writing the test: the first version of it, with only 4
  symbols, "failed" for that reason, not because the fix was wrong).
- **MAJOR — root/import commits manufacturing spurious co-change pairs.**
  `co_change_raw` passed `--root` to `git diff-tree` so the repository's
  first commit's file list would count toward co-change tallies. Files
  "added together once" at repo init is not evidence of repeated
  maintenance coupling, and could produce artificially high `strength`
  (e.g. `2/2 = 1.0`) between two files whose only *other* shared commit was
  coincidental. Fixed: dropped `--root`, so a parentless commit's file list
  is silently skipped by `diff-tree` rather than counted — one flag removed,
  not added. Pinned by `root_commit_does_not_manufacture_a_co_change_pair`.

Three findings were reviewed and intentionally left as documented tradeoffs
rather than code changes:

- **`diff_text`/`run_git` has no output-size bound** — inherited from the
  pre-existing `diff_files` code `oxide review` already used; `--git` on
  `query` newly reaches the same code path, but the underlying
  characteristic (a `git diff` call scales with the diff itself) isn't new.
  Streaming/bounding it properly is a bigger change than this feature's
  scope; `--unified=0` already strips context lines, which bounds it to
  changed-line volume rather than full file content.
- **`co_change_raw`'s `wait_with_output()` buffers a commit's full file list
  before the per-commit cap discards it** — real, but bounded by the
  50-commit window; a pathological single commit touching an enormous
  number of files within that window would cost more memory/time than
  ideal before being correctly discarded. Streaming would need to parse
  `diff-tree`'s output incrementally to abort a single commit's tally
  early — worth doing if this shows up in practice, not preemptively.
- **`review.rs` loads `symbol_relations` (via `snapshot_with_relations`)
  though its `neighbors()`-only structural expansion never reads
  `calls`/`bases`** — a real, avoidable extra query, but review is a
  one-shot CLI process (never exposed over MCP) so there's no cross-call
  cost to amortize, and the alternative (`engine.snapshot()`) silently
  swallows a failed load instead of erroring, which `review.rs` explicitly
  should not do. Kept as-is; not worth trading away error visibility for.

The stale-symbol-mapping and false-co-change concerns Codex was asked to
focus on were otherwise already covered — see Limitations above.
