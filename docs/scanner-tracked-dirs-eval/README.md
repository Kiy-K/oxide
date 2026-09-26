# Git-tracked files inside name-skipped directories — scanner experiment

**Production verdict: reject Git-tracked overrides for generic skipped
directories.** Both challengers (`tracked`, `narrow`) are rejected and the
production scanner is unchanged. Measured reasons (§2–§6):

1. **Insufficient cross-repo benefit** — formerly skipped gold reaches the
   `context` pack in 3 of 14 scoreable potential-gain tasks, all in one
   repository (clap); flipt, dayjs and zstd gain nothing.
2. **Control regressions** — 2 of 23 control tasks lose context file
   coverage (axios: committed `dist/` bundles displace `lib/` gold; bat: a
   corpus-wide BM25 statistics shift displaces README.md).
3. **Line/span degradation** — on the affected set, line coverage falls
   0.145 → 0.122 and span coverage 0.156 → 0.127 as clap's 1,000-line
   `App`/`Arg` symbols take pack slots from gold lines.
4. **DB/storage growth** — `index.db` +23–26% on clap, +119% on axios.
5. **Literal-search cost** — a per-walk `git ls-files` adds 21–25% to
   literal search on vscode (12–28% on mui, which has no skipped
   directories at all — the cost of the check itself).
6. **Watcher/discovery complexity** — a production rule must also change
   the watcher's `has_denied_ancestor`, `service.rs`'s `scan_repo` freshness
   check and literal search, puts a `git` subprocess in every walk, and
   leaves a new, not-yet-added file under `src/build/` invisible until
   `git add`.

The omission itself is real (clap's core `src/build/` is ~19% of its
symbols and invisible to `main`); determinism passes; the negative result
and every raw measurement are preserved here.

## Results

### 1. Current omission behavior

`scanner.rs::DENYLIST_DIRS` prunes a directory by *name* at any depth,
tracked or not. Offline screens (`scripts/policy_screen.py` on 59
checkouts with contents; `scripts/tree_screen.py` over all 66 ContextBench
repositories' trees):

| | repos adding files | files `tracked` would index | of which dependency paths |
| --- | --- | --- | --- |
| all 66 ContextBench repos (tree screen, one commit each) | 14 | 349 (`narrow`: 252 in 11 repos) | 66 (Django's vendored jQuery/select2, NodeBB/openlibrary `vendor/`, prettier `node_modules` fixtures); `narrow`: 0 |

By directory name (`tracked`): `build` 181, `target` 65, `vendor` 56,
`coverage` 31, `node_modules` 10, `dist` 6. Most `build`/`target` additions
are real source (clap `src/build/`, vscode `build/*.ts` scripts, bat's
`build/*.rs`, sphinx's `target` test packages, flipt `build/`); the clear
build-output false inclusion is axios's committed `dist/` bundles.

**Affected tasks: 20, not 21** (the SCIP report's count, since corrected):
clap ×10, flipt ×4, zstd ×2, ansible, dayjs, mui, vue. 15 have tracked,
indexable gold under a skipped directory; the ansible one has a **null
problem statement in ContextBench** (unqueryable in every arm), leaving
14 scoreable potential-gain tasks — and it was one of only four non-clap
ones, so a hit there could have met criterion 1's "≥ 2 repositories". mui
and vue's gold is untracked `node_modules` — neither policy changes their
index; mui (40k files) is excluded from the full pipeline for that reason
and used only for the literal-search latency probe.

### 2. Quality — affected tasks (`raw/affected_eval.jsonl`, 18 scoreable)

`tracked` and `narrow` index identical file sets on every affected task,
and their packs are byte-identical.

| | main | tracked / narrow |
| --- | --- | --- |
| tasks where formerly-skipped gold enters the `context` pack | — | **3, all clap** (`1cec09c7`, `1db0c403`, `2cd03658`) |
| context file coverage, mean | 0.197 | **0.290** (6 gains, 0 losses — all clap) |
| context line coverage, mean | 0.145 | **0.122** (clap `2a1daf41` 0.34 → 0, `6d94a3ab` 0.44 → 0, `07a7e898` 0.35 → 0.21) |
| flipt / dayjs / zstd gold under `build/` reaching the pack | — | 0 (search top-10: flipt `153337fe` +1 file) |

The gain is real but narrow and it is paid for in lines: clap's `App`/`Arg`
are 1,000+-line symbols that fill the per-file and primary slots.

### 3. Quality — controls (`raw/controls_eval.jsonl`, 23 scoreable of 25)

| | main | tracked | narrow |
| --- | --- | --- | --- |
| context file coverage, mean | 0.366 | 0.351 | 0.351 |
| tasks losing file coverage | — | **2** | **2** |
| context line coverage, mean | 0.223 | 0.222 | 0.222 |
| dependency-path items in any pack | — | 0 | 0 |
| packs whose item list changed | — | 10 | (alias of tracked/main except ansible) |

- **axios `98bbaed2`**: committed `dist/axios.js`, `dist/browser/axios.cjs`,
  `dist/esm/axios.js` enter the pack and displace `lib/` gold (file coverage
  0.33 → 0.17). `narrow` keeps `dist`, so it fails the same way. The
  pre-registered generated/minified heuristics do not flag these bundles
  (normal line lengths, no marker) — they are the build-output false
  inclusion the policy was supposed to keep out.
- **bat `7df7e1c0`**: no new-directory item enters, but adding four
  `build/*.rs` files shifts corpus-wide BM25 statistics and README.md
  (gold) is replaced by CONTRIBUTING.md (0.33 → 0.17). Every newly indexed
  file changes scores for the whole repository; 12 of 22 changed packs
  differ in scores only.
- vscode `16d1ff7a`: compiled `build/lib/eslint/*.js` twins of `.ts`
  files enter the pack (gold coverage was 0 in both arms).
- Django: 65 vendored files indexed, none reached a pack.
- prettier ×2: **`oxide index` aborts with a thread stack overflow in every
  arm including `main`** (5,579 files) — a pre-existing bug, unrelated.
- **clap controls** (added after review: the tree screen saw a clap commit
  without `src/build`, so clap had none): the first two non-affected clap
  instances whose commit tracks `src/build/` (`0e4346f7`, `0e4e102e`,
  `raw/controls_clap_eval.jsonl`). `App`/`Arg` enter one pack; no coverage
  change in either task (file 0.5 → 0.5, line 0.5 → 0.5 mean).

### 3b. Span recall and candidate channels

| (context packs, main → tracked) | affected (18) | controls (23) | clap controls (2) |
| --- | --- | --- | --- |
| span coverage, mean | 0.156 → **0.127** | 0.233 → 0.232 | 0.500 → 0.500 |
| direct items via lexical + semantic | 80 → 83 | 113 → 114 | 7 → 7 |
| direct items via lexical only | 15 → 12 | 10 → 9 | 4 → 4 |
| direct items via semantic only | 0 → 0 | 0 → 0 | 0 → 0 |
| structural-only items | 31 → 29 | 47 → 46 | 3 → 2 |
| direct hits in newly indexed files | — → 15 | — → 2 | — → 0 |

Newly indexed files enter mostly as *direct* lexical+semantic hits (clap's
`App`/`Arg` rank on both channels), taking slots from lexical-only and
structural items; no channel changes character. Literal search returns
the 200-hit cap in every arm on all three probes (`raw/literal_latency.json`),
so these probes measure cost, not result changes.

### 4. Operational (`raw/*_eval.measure.jsonl`; arm order rotated per task)

| per task, tracked vs main | median | max |
| --- | --- | --- |
| cold index wall (one run per arm) | affected +35% · controls +2.4% | +54% clap · **+76% axios** |
| peak RSS (process tree) | +4% · +0.2% | +26% (dayjs, small base) |
| `index.db` size | affected +23% · controls +1.0% | **+119% axios** |
| ordinary single-file update (median of 3) | +18.5 ms · +8 ms | +157 ms (sphinx: 2.15 → 2.30 s) |
| `context` latency (median of 5) | +1.6 ms · +1.5 ms | +140 ms on Django's 1.9 s (vscode −29 ms: large-repo noise) |
| hybrid `search` latency | +2.3 ms · +2.2 ms | +32 ms |
| literal search (`raw/literal_latency.json`) | vscode `createDecorator` **+21%** (`narrow` +25%) | mui `TODO` +28%, `useTheme` +12% — see below |

The clap cost is the price of completeness — +19% symbols of real library
code (CPU time +48–71% across its 10 tasks) — not noise; axios's is noise.
Cold wall time is a single run per arm and noisy (openlibrary +29% wall on
+0.4% symbols), so criterion 3 is judged on the deterministic `index.db`
size. The literal-search cost is the challenger's per-walk `git ls-files`:
mui has *no* name-skipped directories, so its +12–28% is that call alone —
a version that asked Git only on meeting a skipped directory would pay ~0
there; vscode (+21%, tracked `build/`) is the meaningful number.

### 5. Determinism (`raw/parity.json`)

- Challenger with the policy unset ≡ real `main` binary: `symbols` rows and
  `context`/`search` JSON byte-identical on dayjs, flipt, clap, requests.
- `oxide eval --config fixtures/benchmark.json`: identical rows under the
  `main` binary (twice) and the challenger unset / tracked / narrow, as
  written by `scripts/parity.py`. Row *order* varies between processes of
  the same binary — `src/eval.rs:14,113` iterates `config.repos`, a
  randomly seeded std `HashMap` (pre-existing; the per-mode means are
  order-independent) — so rows are compared sorted, and the raw comparison
  is recorded too. The first parity file, whose sorted comparison was done
  by hand, is kept in `raw/discarded/`.
- Every request ran 5× with byte-identical output; `tracked` ≡ `narrow`
  byte-for-byte on all 18 affected tasks where their scan sets are equal.

### 6. Gate

| criterion | result |
| --- | --- |
| 1 benefit: ≥ 3 tasks across ≥ 2 repos; affected file coverage not down | **fail** — 3 tasks, 1 repo (file coverage up, line coverage down) |
| 2 no regression: ≤ 1 control loses file coverage; line Δ ≥ −0.01; no dependency/generated items | **fail** on the losses alone — 2 (axios, bat; both deterministic); line Δ −0.001 and "no flagged items" pass as worded, though the axios `dist` bundles are the build-output false inclusion the flags were meant to catch |
| 3 operational: cold & DB ≤ +10%, update ≤ +20 ms, request ≤ +10 ms, literal ≤ +15% | **fail** — `index.db` size (clap +23–26%, axios +119%); literal vscode +21%; request latency passes; update median +18.5 ms (`narrow` +20.5 ms on identical files: noise at the threshold) |
| 4 determinism | pass |
| 5 explainable and modest | fail in practice — a production version must also change the watcher's `has_denied_ancestor`, `service.rs`'s `scan_repo` freshness check and literal search, adds a `git` subprocess to every walk, and makes an *untracked* new file under `src/build/` invisible until `git add` |

The reject does not rest on the softest criteria: criterion 1's one-repo
failure depends on ansible `788af515` being unqueryable, and the mui
literal cost is an artifact of the challenger's unconditional `git
ls-files` — but criterion 2 (two deterministic control losses) and
criterion 3 (`index.db` size, deterministic) fail independently of both.

**Production verdict: reject** (`tracked` and `narrow`). Negative result
preserved; scanner unchanged. What the data does support is narrower than
either policy: the loss is one repository's source directory that happens
to be named `build`. A rule that recovers it without importing `dist/`
bundles or reshuffling BM25 statistics was not pre-registered and is not
proposed from 3 tasks in 1 repository.

## Reproducing

```bash
S=docs/scanner-tracked-dirs-eval/scripts; PY=eval-agent/.venv/bin/python
$PY $S/affected.py --checkout > raw/affected.jsonl        # 20 tasks + checkouts
python3 $S/policy_screen.py <checkouts...> > raw/policy_screen.jsonl
python3 $S/tree_screen.py <cb_repos.json> ~/.cache/oxide-scanner-screen > raw/tree_screen.jsonl
# challenger: git worktree add <dir> d1333a9 && git -C <dir> apply challenger.patch && cargo build --release -j 2
$PY $S/parity.py <main oxide> <challenger oxide> <work> raw/parity.json <checkouts...>
$PY $S/challenger_eval.py raw/affected_run.jsonl <challenger oxide> <work>/affected raw/affected_eval.jsonl
$PY $S/challenger_eval.py raw/controls.jsonl <challenger oxide> <work>/controls raw/controls_eval.jsonl --skip-equal
$PY $S/challenger_eval.py raw/controls_clap.jsonl <challenger oxide> <work>/controls_clap raw/controls_clap_eval.jsonl
$PY $S/analyze.py raw/affected_eval.jsonl; $PY $S/analyze.py raw/controls_eval.jsonl
python3 $S/literal_latency.py <challenger oxide> raw/literal_latency.json 5 <mui checkout>=useTheme,TODO <vscode checkout>=createDecorator
```

Each measured run's full stderr (`measure.py` writes one file per run) is
kept in `raw/stderr.tar.gz`; the `*.measure.jsonl` records carry its tail.

Discarded: `raw/discarded/1-affected-relative-measure-path.jsonl` (a
relative measurement-log path broke every record);
`raw/discarded/2-parity-eval-compared-raw-then-sorted-by-hand.json`
(superseded by the script-written `raw/parity.json`). Harness defects fixed
before the recorded run: an unanchored `rsync --exclude target` that would
have deleted ansible's tracked `target/` gold directory from every copy;
per-arm incremental probe files; single-sample incremental timing.

## Hypothesis (falsifiable)

> Allowing Git-tracked source files inside directories OXIDE currently skips
> by name (`scanner.rs::DENYLIST_DIRS`: `build`, `dist`, `out`, `target`,
> `vendor`, `coverage`, `node_modules`, …) improves ContextBench context
> recall on the affected tasks without unacceptable indexing, storage,
> latency, memory or noise regressions.

Scored with the corrected ContextBench scorer (`docs/contextbench-scorer-fix/`).

## Policies

| arm | rule |
| --- | --- |
| `main` | current: a denylisted directory name is pruned at any depth |
| `tracked` | a denylisted directory may be descended when it contains Git-tracked files; inside it only tracked files are kept; VCS dirs (`.git`, `.hg`, `.svn`) never. Everything else unchanged: `.gitignore`/`.ignore`, hidden entries, file-name/suffix denylist, size caps, binary sniff |
| `narrow` | `tracked`, restricted to `build`, `dist`, `out`, `target` (output-style names that are also common source directory names); dependency/cache names (`vendor`, `node_modules`, `venv`, `coverage`, `__pycache__`) stay pruned |

Implemented as a diagnostic challenger only (`challenger.patch`, built in a
separate worktree at `d1333a9`), selected by `OXIDE_SCANNER_POLICY`; the
`main` arm is the same binary with the variable unset, and the real `main`
binary is a separate parity check.

## Task sets (fixed before running)

- **Affected (20)**: every `full` instance whose gold includes a file under
  a name-skipped directory (`raw/affected.jsonl`). 15 have tracked,
  indexable gold there (clap ×10, flipt ×3, ansible, dayjs) — the
  *potential-gain* set; 5 cannot gain (flipt `997c7afd`: Dockerfile; zstd ×2:
  CMake/VS project files; mui, vue: untracked `node_modules`).
- **Controls (25)**: for each of the 14 repositories where the breadth screen
  (`raw/tree_screen.jsonl`, all 66 ContextBench repos) finds files `tracked`
  would add, the first two `full` instance ids (sorted) not in the affected
  set (`raw/controls.json`). These are where the index changes but gold is
  not under a skipped directory — where noise and displacement would show.

## Decision gate (pre-registered)

A policy is accepted for a production change only if **all** hold:

1. **Benefit** — in the 15 potential-gain tasks, a gold symbol from a
   formerly skipped directory enters the final `oxide context` pack
   (non-module item overlapping the gold span) in **≥ 3 tasks spanning
   ≥ 2 repositories**, and mean context file coverage over the 20 affected
   tasks does not decrease.
2. **No regression** — on the 25 controls: at most **1** task loses context
   file coverage, mean context line coverage changes by **≥ −0.01**, and
   **no** item from a dependency path (`vendor/`, `node_modules/`,
   `third_party/`) or a flagged generated/minified file enters any pack.
3. **Operational** — per task, cold index wall and `index.db` size grow
   **≤ 10%**; an ordinary single-file update grows **≤ 20 ms** (median);
   `context`/`search` request latency grows **≤ 10 ms** (median); literal
   search on the largest repository (mui, 40k tracked files) grows **≤ 15%**.
4. **Determinism** — challenger-unset ≡ `main` binary (symbols rows and
   packs), `oxide eval --config fixtures/benchmark.json` identical under all
   three policy values, repeated requests identical, and `tracked` ≡
   `narrow` byte-for-byte wherever their scan sets are equal.
5. **Explainable and modest** — the rule states in one sentence, and the
   production change is small enough to keep `walk_repo`, the watcher's
   `has_denied_ancestor`, and the other `scan_repo` callers consistent.

Otherwise the negative result is recorded and the scanner is unchanged.
