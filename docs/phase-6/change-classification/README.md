# AST change classification, dependency invalidation and structural file localization (2026-10-26)

Question: can a parse-level classification of an edit tell which derived rows it can
change, and is that worth building, for indexing on large repositories? And does any
structural (non-entity-term) file signal localize gold files better than the current
ranking? Prior evidence, unchanged here:

- [incremental-indexing](../incremental-indexing/README.md): parse facts are reused per
  digest; resolution and publication stay whole-snapshot.
- [indexing-throughput](../indexing-throughput/README.md) §2, §5: after a one-file edit,
  publication is 56–78% of the warm index, and `CREATE_FTS_INDEX` sets a 3–4 s floor on
  any graph change, because in-place FTS mutation is not exact (§5c). `site` is a global
  ordinal (§5a). Deleting a file changes rows in 1–21 other files, and "no safe per-file
  dependency rule was found" (§5b).
- [file-localization](../file-localization/README.md): NOT VIABLE. Stage 2 (signals) never
  ran. Open lead: "a directory-level localizer that does not reuse the entity terms".

This section is written before any run. No `src/` change, no model, dev data only, no
frozen artifact touched.

## Part A: how often is an edit body-only? (real history)

For each repository below, the newest 300 non-merge commits on `HEAD` that touch a
supported source file (`indexing.dialect`). Each modified source file is parsed before
and after with the production parser (`git cat-file`, no checkout). Classes:

- **`layout`:** the *interface projection* and the *use names* are equal; only byte
  offsets, comments, whitespace or diagnostics differ.
- **`body`:** the interface projection is equal; use names (calls, refs) differ.
- **`interface`:** the interface projection differs, or either side does not parse.

The interface projection is every parse fact that another file's resolution reads,
which is every fact except byte offsets and the use lists that each resolver reads only
inside the owning file's emit loop (`calls`; Go/Rust `refs`). It covers declarations
(kind, name, signature, export/public/test/owner/target/trait/variants, nesting), bound
and rebound names, imports, Rust `mod`s, Go package, Python bases, `receiver`, `made`,
`open`, `assigned` and `attrs`.

A commit is **`structural`** if it adds, deletes or renames a supported file, or changes
a manifest that resolution reads (`go.mod`, `Cargo.toml`, `package.json`,
`tsconfig*.json`, `jsconfig*.json`). Otherwise it takes its worst file class.

Repositories (full clones in `oxide-eval-data/oxide-jev-eval/cb-repos/`): django (Python),
vuejs/core (TypeScript), cli/cli (Go), clap (Rust).

## Part B: is the rule sound? (full derivation diff)

Up to 25 `layout`/`body` file edits and 10 `interface` edits per repository, spread
evenly over Part A's list. Each is applied to the repository's `HEAD` capture in memory
(the file's bytes replaced by its before and its after version), then derived twice,
with cached facts. Measured per edit:

- `other_files`: files other than the edited one owning a changed row (entity or
  relation; `site` ignored; owner as in `indexing-throughput/scripts/drift.py`);
- `documents`: entities whose FTS `document()` changed;
- `rows`: changed rows of the edited file.

**Rule:** a `layout`/`body` edit re-derives only the edited file's rows, and leaves every
FTS document unchanged. It is **SOUND** on a language when every sampled `layout`/`body`
edit has `other_files = 0` and `documents = 0`. A single violation makes it UNSOUND for that
language, and the responsible fact is reported.

## Part C: is it worth building?

**WORTH A DESIGN** if, on at least 3 of 4 languages, the rule is SOUND and `layout` +
`body` are at least 50% of modified-file edits and at least 30% of commits. Otherwise
**NOT WORTH IT**. Savings are reported as an upper bound **inferred** from
indexing-throughput §2 and §9b. Building them needs format and storage changes that this
study does not make.

## Part D: structural file localization (diagnostic only)

This runs the existing `signals` arm of `file-localization/scripts/loc_run.py` (P0 to P4,
unchanged) on the py and ts dev splits, plus one new signal:

- **`P5 dir-path`:** BM25 (k1 1.2, b 0.75) over directories. A directory's document is
  `terms()` of its path plus the file names directly in it; it uses no entity terms.
  Files rank by (directory score, `P2` path score, path).

Measure: gold-file recall@{1, 3, 10, 20}, macro over tasks, overall and on vscode. The
lead **stays open** if `P2` or `P5` raises recall@10 by at least 0.10 over `P0` on ts
overall. Otherwise it **closes**. This cannot change file-localization's verdict, and
no end-to-end arm runs.

## Reproduce

    systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 \
      uv run python docs/phase-6/change-classification/scripts/classify.py REPO OUT.json
    # same caps:
    uv run python docs/phase-6/change-classification/scripts/soundness.py REPO OUT.json SOUND.json
    uv run python docs/phase-6/file-localization/scripts/loc_run.py ts OUT signals

# Results (2026-10-26)

**Verdicts:** the body-only rule is **SOUND** on all four languages, but under the
preregistered criteria building it is **NOT WORTH IT** (1 of 4 languages clears the
share bars; 2 of 4 under an exploratory refinement). The structural-localization lead
**closes**. Numbers are measured unless marked *inferred*. Raw data is in `results/`.
`src/` is unchanged, and no frozen artifact was touched.

Setup note: the `cb-repos` clones are blobless (`--filter=blob:none`). Lazy fetching one
object at a time stalled `classify.py`, so the blobs named by `git log --raw` for the
newest 2,000 non-merge commits were fetched in one batch per repository first. Every run
used `GIT_NO_LAZY_FETCH=1`, and 0 blobs were missing.

## Part A: edit classes over 300 commits per repository (measured)

`class` is the preregistered projection. `refined` is **exploratory, added after seeing
the data**: it also blanks function-local `bound` names (and Python `made`/`receiver` in
function scopes). Those only feed the owning file's lexical lookups, and a breakdown of
`interface` edits found them to be the second-largest cause. On Go, 257 of 797
`interface` edits were function-local `bound` alone.

| repo | file edits | layout | body | **layout+body** (refined) | commits layout+body (refined) | structural commits |
|---|---:|---:|---:|---:|---:|---:|
| django (py) | 1,005 | 18% | 9% | **27%** (33%) | 16% (19%) | 7% |
| vuejs/core (ts) | 736 | 22% | 16% | **39%** (44%) | 20% (24%) | 5% |
| cli/cli (go) | 1,507 | 24% | 23% | **47%** (58%) | 33% (40%) | 13% |
| clap (rust) | 627 | 44% | 8% | **52%** (57%) | 36% (44%) | 21% |

The cause of `interface`, counting each edit once per cause: a declaration or signature
change accounts for 54–60% of `interface` edits on every repository. Import changes are
next: Go 313 of 797, TypeScript 165 of 452, Rust 150 of 301; on Python, module/class
imports account for 189 of 732. Most real commits change a declaration's signature,
which is also an FTS `document()` change. So they are interface changes by any
definition.

## Part B: soundness (measured)

30 refined layout/body edits and 10 interface edits per repository, applied to the
`HEAD` capture and derived in full before and after:

| repo | layout/body edits (pre / refined-only) | with other files changed | with documents changed | interface edits: other files / documents changed |
|---|---:|---:|---:|---:|
| django | 26 / 4 | **0** | **0** | 2 / 5 of 10 |
| vuejs/core | 29 / 1 | **0** | **0** | 0 / 7 of 10 |
| cli | 23 / 7 | **0** | **0** | 1 / 8 of 10 |
| clap | 28 / 2 | **0** | **0** | 1 / 6 of 10 |

- **SOUND** on all four languages: across 120 layout/body edits, 0 rows changed outside
  the edited file and 0 FTS documents changed. The check can detect a violation: 4
  interface edits did change other files, and 26 of 40 changed documents.
- As expected, an edited file's own rows all change (24–3,094 rows). Spans shift, and
  `SourceRef` carries the digest.
- Limits: 30 edits per language is a sample, not a proof. Each edit is applied in a
  `HEAD` context, not in its own commit's tree (*inferred* harmless: the rule's claim
  does not depend on the context). Django's run took 983 s (two full derivations per
  edit).

## Part C: what it could buy (measured probe, inferred savings)

- **FTS under a non-indexed SET (measured).** `fts_nonindexed.py` on nushell (14,714 rows,
  where in-place `terms` mutation failed in indexing-throughput §5c) SET a non-indexed
  column on 735 rows inside one transaction. **0 of 500** query `(key, score)` lists
  differed from a fresh build, and the stored values were equal. One corpus and one seed
  only; §5c showed that size matters, so a larger corpus would need its own run.
- **Inferred upper bound.** For a layout/body commit, an in-place delta would only need
  to replace the edited file's `Entity` spans and `Rel` rows. It would keep the
  previous FTS index (no `CREATE_FTS_INDEX`: 3.1 s transformers, 4.3 s vscode, §2) and
  skip most CSV/COPY. Reusing other files' resolved rows would skip most of resolution
  (5.9 s transformers). On the warm one-file path (§9b: 15–17 s on the large corpora),
  that is at most about 60–75% of such a commit's index time. Weighted by the commit
  shares above (16–44%), the expected saving per commit is about 10–30% (*inferred*).
- **What building it needs** (all outside this study): a stable relation identity in
  place of the global `site` ordinal (§5a; a versioned format change), a copy-on-write
  graph delta with a readback-equality check, cached per-file resolved rows keyed by the
  interface projection, and the classifier as the gate. Structural and interface commits
  keep today's full path.

**Decision (preregistered): NOT WORTH IT.** Only clap clears both bars (≥50% of edits,
≥30% of commits); with the refinement, cli does too, which is still 2 of 4. Python and
TypeScript commits are mostly interface changes. Revisit if indexing on every commit
becomes the dominant cost for Go or Rust repositories, where 40–44% of commits are
body-only. The design would then start from stable relation identity, which every
incremental path needs anyway (§5d).

## Part D: structural file localization (measured, dev only)

Gold-file recall, macro over tasks (ts: 25 dev tasks with verified gold; py: 15):

| signal | ts @1 | ts @3 | ts @10 | ts @20 | vscode @10 (n=13) | py @10 |
|---|---:|---:|---:|---:|---:|---:|
| P0 entry (production) | 0.300 | 0.397 | **0.467** | 0.550 | 0.058 | 0.867 |
| P1 vote | 0.253 | 0.363 | 0.453 | 0.477 | 0.019 | 0.956 |
| P2 path BM25 | 0.190 | 0.190 | 0.200 | 0.200 | 0.019 | 0.556 |
| P3 graph | 0.253 | 0.363 | 0.417 | 0.490 | 0.019 | 0.900 |
| P4 dir (entity terms) | 0.220 | 0.370 | 0.417 | 0.467 | 0.000 | 0.789 |
| P5 dir-path (new) | 0.190 | 0.257 | 0.257 | 0.257 | 0.032 | 0.544 |

- **The lead closes.** The non-entity-term signals (P2, P5) are worse than P0 by 0.21–0.27
  at @10 on ts. On vscode, where the failures sit, no signal reaches 0.06 at @10. A
  query's terms rarely name the gold file's directory or file name. Directory structure
  without entity terms carries less signal, not different signal.
- P1 does beat P0 on py at @10 (+0.089), which is below the 0.10 bar and on the benchmark
  where localization headroom was smallest. File-localization's NOT VIABLE stands.
- Cost: the ts run took 322 s and peaked at 1.30 GB RSS; py took 7 s.

## Negative results

- Preregistered interface projection: body-only is 16–36% of commits. Function-local
  bindings were a large false-interface source on Go.
- No structural or path-only file signal localizes better than the entity ranking; P5
  is worst at depth.

## Reproduce (as run)

The preregistered commands above, plus the blob prefetch and the two probes. From the
repository root, one capped job at a time (`CAP` = `systemd-run --user --scope -q -p
CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0`). `REPO` is a clone that has history;
the `cb-repos` clones are blobless, so fetch their blobs first:

    git -C REPO log -n 2000 --no-merges --raw --no-abbrev --format= \
      | awk '$5 ~ /^M/ {print $3; print $4}' | sort -u \
      | xargs -n 400 git -C REPO -c fetch.negotiationAlgorithm=noop fetch -q --no-tags \
          --no-write-fetch-head --filter=blob:none origin
    export GIT_NO_LAZY_FETCH=1
    cd docs/phase-6/change-classification/scripts
    $CAP uv run python classify.py REPO classes.json          # Part A
    $CAP uv run python soundness.py REPO classes.json sound.json   # Part B
    $CAP uv run python fts_nonindexed.py SCRATCH NUSHELL_CORPUS    # Part C probe
    cd -
    $CAP uv run python docs/phase-6/file-localization/scripts/loc_run.py ts OUT signals
    uv run python docs/phase-6/change-classification/scripts/signal_recall.py OUT/signals.json

`results/` holds the outputs of these commands: `classes-*.json`, `sound-*.json` (with
each repository's `HEAD`), `fts-nonindexed-nushell.json` and `signals-{py,ts}.json`.
Part A's commit lists start at, and Part B's edits apply to, these `HEAD`s: django
`a461af8ce487`, vuejs/core `4ab865a848a1`, cli/cli `6fc1c29d5477`, clap `4be56132cf7a`.
The nushell corpus is `nushell__nushell@399319476aa4`.
