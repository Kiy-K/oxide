# Git diff algorithm and host diff config: effect on Git-derived context (#35)

Benchmark-only. Production `gitutil`, `gitctx`, scoring and CLI/MCP/JSON are
unchanged. Baseline: `origin/main` at `a573a59`.

Question (#35): `gitutil::diff_text` runs `git diff --unified=0` without a
pinned algorithm, so `oxide review` and `oxide context --git` follow the host's
`diff.algorithm`. Should OXIDE pin one, and which?

## Setup

- git 2.43.0, Python 3.11.15, release `oxide` built from `a573a59`.
- `OXIDE_EMBED_NATIVE=hashed` (offline, deterministic), `NO_COLOR=1`, `OXIDE_TELEMETRY=0`.
- **Host-config isolation** (`scripts/gitenv.py`): every git process,
  including the ones `oxide` spawns, runs with `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_CONFIG_GLOBAL=/dev/null`, an empty `HOME`/`XDG_CONFIG_HOME`, all
  inherited `GIT_*` variables removed, `LC_ALL=C` and `TZ=UTC`. The variant
  under test is injected only through `GIT_CONFIG_COUNT/KEY/VALUE`. The cloned
  repos carry no `diff.*` in their `.git/config`. The host itself has no
  `diff.*` config either.
- Raw diffs use the exact `diff_text` argv: `git diff --unified=0 --no-color
  --src-prefix=a/ --dst-prefix=b/ <range>`. `scripts/gitenv.py::parse_unified`
  is a line-for-line port of `gitutil::parse_unified`/`parse_hunk_header`. It is
  checked against that module's unit-test diff, and it is used only to
  *select* commits. Every end-to-end number below comes from the real binary.

Variants: `default` (no setting), `myers`, `histogram`, `patience`. In the
screen only: `minimal`, plus three non-algorithm knobs,
`diff.indentHeuristic=false`, `diff.interHunkContext=3` and
`diff.renames=false|copies`.

### Repos and cases

| Case | Language | SHA (HEAD of clone) | Commits screened |
|---|---|---|---|
| `fixtures/py_repo` (`retry.py`, the #35 case) | Python | fixture @ `a573a59` | 1 worktree state |
| `fixtures/ts_repo` (`retry.ts`) | TypeScript | fixture @ `a573a59` | 1 |
| golden `roles` repo | Python | (inline) | 1 |
| pallets/flask | Python | `d73fa1cd` | 300 |
| pylint-dev/pylint | Python | `8e75f579` | 300 |
| spf13/cobra | Go | `adbc8813` | 300 |
| colinhacks/zod | TypeScript | `004d800c` | 300 |
| Kiy-K/oxide | Rust | `a573a59` | 300 |

The fixture states are built exactly as `tests/candidate_output_golden.rs::repo()`
builds them, with the golden's queries.

**Stage A** (`stage_a.py`): for the latest 300 non-merge commits per repo
(`C^..C`), the raw diff and parsed added ranges per variant, with the git call
timed.

**Stage B** (`stage_b.py`, `select.py`): end-to-end. The selection is every
commit (capped at 30) whose parsed ranges *on files OXIDE indexes* differ
between any two of myers/histogram/patience, plus 5 random controls with
identical ranges (seed 35). Each state is checked out as HEAD=`C^` with
index and worktree at `C` (`git reset --soft C^`), so `git diff HEAD` equals
`C^..C`. `oxide index` runs once per state, then for each variant:
`review --diff "" [--json]` and `context "<commit subject>" --git [--json]`.
Fixtures also run `--blast-radius` and `--profile quality`. `myers` runs twice
(`myers_2`) as a run-to-run determinism control. The selections are in
`selection/`.

**Stage B2** (`stage_b2.py`): the same end-to-end harness for
`diff.interHunkContext=3` and `diff.indentHeuristic=false`, on 10 random
commits per repo whose ranges those knobs change.

**Pin check** (`pinned_check.py`): a hostile host config combines all knobs
plus `diff.external`, a textconv driver via `core.attributesFile`,
`mnemonicPrefix`, `noprefix`, `suppressBlankEmpty`, `color.ui=always`,
`diff.relative` and `diff.context`. It is applied to the current argv and to
the argv plus `--diff-algorithm=myers --indent-heuristic
--inter-hunk-context=0 --find-renames --no-ext-diff --no-textconv`, over
100 commits per repo. An earlier draft also listed `--no-relative`; it was
dropped. `diff_text` always runs at the repo root, where `diff.relative` has
no effect (reviewer: 0/40 commits changed), and the flag needs git ≥ 2.28
(inferred, not verified).

Raw results are in `results_*.txt`. To reproduce, run from an empty work dir
holding `repos/<name>` clones: `stage_a.py repos/<r> 300 stage_a_<r>.json`,
then `select.py`, `stage_b.py repos/<r> sel_<r>.json <out>`, `fixtures_b.py
<out>`, `stage_b2.py <r>`, `pinned_check.py`. A re-run of `fixtures_b.py` from
these copies reproduced every output exactly, except the absolute path in the
human review header.

## Results

### Controls

- `default` and `myers` are byte-identical on all 1,500 commits (raw diffs)
  and on every end-to-end output (measured).
- `myers` vs `myers_2` is identical on every state, so the end-to-end path is
  run-to-run deterministic (measured).
- The 25 control states (identical parsed ranges) gave identical outputs under
  all variants (measured).
- `minimal` equals `myers` on parsed ranges in all 1,500 commits. One raw
  diff differs, in a non-indexed file.

### Stage A: raw hunks (300 commits per repo)

Commits whose parsed added ranges differ, counting only files OXIDE indexes:

| Repo | myers↔histogram | myers↔patience | histogram↔patience | myers↔noindent | myers↔interHunkContext=3 * |
|---|---|---|---|---|---|
| flask | 11 | 8 | 6 | 2 | 117 |
| pylint | 11 | 9 | 9 | 4 | 86 |
| cobra | 12 | 10 | 9 | 1 | 73 |
| zod | 24 | 25 | 19 | 7 | 138 |
| oxide | 43 | 42 | 20 | 42 | 149 |

\* The interHunkContext column counts all files, not just indexed ones.
`diff.renames=false` changes 1–8 commits per repo.

That is 2–14 % of commits per algorithm pair. `diff.indentHeuristic=false`
reaches the same order of magnitude (14 % on oxide). `diff.interHunkContext`
dominates at 24–50 %.

### Stage B: what reaches the user (119 real states, 94 with differing ranges)

| Output | Effect across myers/histogram/patience |
|---|---|
| `changed_files` | never differs |
| changed-symbol **set** | differs in **3/119** states (flask `28d5a4d7`, pylint `9f08fc7b`, oxide `d785ede2`) |
| changed-symbol `added_lines` / order | mostly the `(+N lines)` count; order changes in only a few states: review JSON and human output both differ in 62/119. The \|Δ added_lines\| median is 4, the max 239 |
| review `related` | differs in 3 states (the same three) |
| `context --git` **items** (symbol, order, role) | 0/119 with the one query per state. This is not "never": git seeds take a flat `0.28 × top seed` for the first 8 changed symbols, so only a change in the set or the top 8 can move items. The reviewer re-ran the 3 set-level states with generic queries, and flask `28d5a4d7` gains `tests/conftest.py#app` under histogram/patience. So the right word is **rarely** |
| `context --git` JSON/human | differs in 34/119. In every case but one, only the `git-changed(+N)←file` reason text differs. In one (flask `28d5a4d7`) the `Flask` class gains a `git-changed` reason and a higher score, and `omitted` gains one entry. In one (pylint) only the `omitted` order differs |
| total changed symbols | myers 2537, histogram 2536, patience 2539 |

**Fixtures.** On `py_repo/retry.py`, myers gives one hunk `+23,23` and
histogram/patience give `+23,3` and `+27,19`, because they keep the blank line
26 as context. The changed-symbol **set is identical**. Review output differs
only in order (`RetryPolicy` first vs `RetryPolicy.__init__` first) and in
`RetryPolicy (+23)` vs `(+19)`. This is the golden difference #35 recorded.
`context --git` is byte-identical under all three algorithms, for every query
and profile. `ts_repo` and `roles` are identical under every variant.

**The three set-level cases are ambiguous alignments, not one algorithm being
right:**

- pylint `9f08fc7b`: duplicated `def aaaa(self): """overridden form Abstract"""`
  stubs. Myers reports `ConcreteB.aaaa` and misses `ConcreteC.aaaa`; histogram
  does the opposite. Both methods are new code.
- oxide `d785ede2`: trait methods were moved from `IndexBackend` into
  `IndexRead`/`IndexWrite`. Each algorithm keeps a different moved block as
  "unchanged", and all three disagree.
- flask `28d5a4d7`: histogram and patience place an inserted blank line on a
  class-owned line, so the 1,400-line `Flask` class becomes a changed seed
  (+16). Myers does not. This is arguably noise in the histogram/patience
  output.

No algorithm is consistently more accurate. This was judged by reading these
three cases; there is no automated oracle.

### Stage B2: other host knobs (50 states)

- `diff.interHunkContext=3`: review differs in 38/50 states, the symbol set in
  5 (2 extra symbols, 5 missing), review `related` in 3, context items in 1,
  and context JSON in 27.
- It is also **semantically wrong** for OXIDE. With `-U0`, merged hunks put the
  unchanged lines between them inside the `+start,count` range, and
  `parse_unified` counts those lines as added. Example: flask `89992954`
  `@@ -55,4 +56,6 @@`, which contains 2 context lines.
- `diff.indentHeuristic=false`: 0/50 end-to-end differences. Only 4 of the 50
  sampled states had differing ranges, so this is weak evidence.

### Runtime

- `git diff` per commit has a median of 3.0–4.5 ms for every algorithm. The
  medians are within ±0.3 ms of each other, and the totals over 300 commits
  are within ±5 %.
- End-to-end `review` medians are within run-to-run noise: for example
  myers 113, histogram 110, patience 114 ms across real states, and `myers_2`
  varies as much as the algorithms do.

Algorithm choice has no material cost.

### Pin check

- Under the hostile config, the **current** argv changes parsed ranges in
  99–100 of every 100 commits.
- That 99–100 figure is driven mostly by `diff.external`. Alone it changes
  40/40 flask commits; today's argv honours it, so a difftastic-style
  `diff.external` silently empties OXIDE's diff evidence. `diff.interHunkContext=3`
  alone changes 19/40 (reviewer, measured).
- A textconv driver alone, with today's argv, runs on matching files: `rev`
  over `CHANGES.rst` hung for more than 2 minutes. In the hostile run
  `diff.external` masked it, so `--no-textconv` is justified by this separate
  check.
- With the pinned flags, the output is **byte-identical** to the isolated
  default on every commit (0/500 differ).
- With the pinned flags and no hostile config, the output is
  **byte-identical** to today's default-config output (0/500 differ). Pinning
  myers therefore changes nothing for hosts with default git config.

## Verdict

**PIN `myers`** (git's default), and pin the other hunk-shaping options with
it. Pinning only the algorithm leaves `diff.interHunkContext` free, and that
setting moves more output, and less correctly, than the algorithm does.

- **Pin:** pinning is justified mainly as robustness against host config,
  not because one algorithm is better.
  - The algorithm alone changes review `(+N lines)` text in about 2–10 % of
    commits, but the changed-symbol set in only about 0.2 % (3 of ~1,500).
  - Other knobs are worse: `diff.external` empties the evidence,
    `interHunkContext` inflates the added ranges, and textconv rewrites or
    stalls the diff.
  - Pinning costs nothing at runtime and changes nothing for default-config
    hosts.
- **Which algorithm:** the evidence is quality-neutral between myers,
  histogram and patience. The tie-break is compatibility. Myers is what every
  default-config host produces today (0/500 byte differences), so only users
  who set `diff.algorithm` themselves see a change. Histogram has no measured
  advantage. That the golden happens to pin it is a test-harness artifact.

**Golden implication (intended change, not a re-baseline).**
- A `--diff-algorithm=myers` argument overrides the `GIT_CONFIG_*` histogram
  pin in `tests/candidate_output_golden.rs`.
- The `py_repo` review section of `fixtures/candidate_output/golden.txt`
  would then change: `RetryPolicy` goes from +19 to +23 and moves first.
- The env pin would become dead, and should be removed or switched in the
  same change.

## Retained limitations

- One query per real state (the commit subject), hashed embedder, default
  budget.
- The worktree form (`git diff HEAD`) was exercised through `--soft` reset
  states, not real dirty trees with untracked files.
- Clean/smudge filters and `core.autocrlf` were not tested.
- Correctness of the three set-level cases was judged by hand.
- The interHunkContext and noindent end-to-end sample is small (10 per repo).
- No Java or C/C++ repos were tested, and only the newest commits were used.
  `md` counts as indexed.
- Unpinned knobs that remain: `diff.renameLimit`, `diff.submodule`,
  `GIT_DIFF_OPTS` and `GIT_ATTR_NOSYSTEM`.
- Separate, algorithm-independent bug: non-ASCII paths are quoted
  (`+++ "b/…"`), so `parse_unified` drops them.
