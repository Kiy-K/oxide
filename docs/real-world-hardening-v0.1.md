# Phase 1.2 real-world hardening (sections A/B/C)

Real repositories that were not used to develop OXIDE's existing test
suite, tested on scratch copies (never in place inside the shared eval
cache) with the release binary. Findings recorded here rather than left in
a session transcript, per the phase's "record repositories tested and why"
requirement.

## Repositories tested

| repo | category | files / symbols | why selected |
|------|----------|-----------------:|---------------|
| `flask` | small Python | 80 / 1,629 | small, real, idiomatic Python package layout |
| `pytest` | medium Python | 253 / 5,611 | medium size, heavy metaprogramming/decorators stresses the Python extractor |
| `django` | larger real-world repo | 2,464 / 32,978 | largest available real Python codebase; doubles as a scaling/perf spot-check |
| `darkreader` | small/medium TypeScript | 197 / 993 | real TS repo, no framework-specific build quirks |
| `code-server` | TS repo with submodule + symlink | 81 / 432 | has a real `.gitmodules` submodule and a real symlink — nested-repo and symlink cases without a synthetic fixture |
| `tailwindcss` | repo with no supported source files | 0 supported files | real repo that happens to be plain JS, not TS — genuine `no_source_files` case |
| `flask` + `darkreader` (constructed) | mixed Python + TypeScript | 191 / 1,363 | no single cached repo is polyglot; combined two real single-language repos under one root to get a genuine mixed-language index |

Synthetic fixtures (constructed locally, not from the cache) covered what
the cached repos didn't naturally exercise: nested git repos, a symlink
loop, and directory names with spaces/unicode/leading dashes — see below.

## Bugs found and fixed

### 1. Directory denylist was dead code (`237a363`)

`scanner.rs`'s `DENYLIST_DIRS` (venv, vendor, build, dist, coverage, ...)
was only ever checked against **files**, never directories — the walker
descended into every non-hidden directory regardless of name, so a `.py`
or `.ts` file underneath `venv/lib/*/site-packages/`, `vendor/`, or any of
the other 15 denylisted-but-non-dotted directory names was indexed as
noise. Only extension filtering and `.gitignore`/dotfile hiding were
actually pruning anything.

**Repro**: a repo with `src/app.py` (1 real file) plus `venv/lib/.../pkg.py`
and `vendor/thirdparty/lib.py` reported `scanned_files: 3` before the fix,
`scanned_files: 1` after. A broader check against all 18 `DENYLIST_DIRS`
entries went from 19 scanned files (18 noise + 1 real) to 1.

**Fix**: directories are now pruned from descent (`WalkState::Skip`) when
denylisted, not just excluded from the file list after the fact.

**Impact on real repos**: none of the 7 cached repos above actually
exhibited the bug in this checkout (their vendor/venv/build directories
either weren't present in the shallow git clones or were already
`.gitignore`d), so the fix didn't change any real-repo symbol count in this
session — but the mechanism was broken and would bite the first repo with
a non-hidden `venv/`, `vendor/`, or `build/` directory containing real
source, which is a common enough layout that it's worth fixing before any
external user hits it. Canonical benchmark unchanged (0.818 / 0.909)
before and after.

### 2. `deleted_symbols` undercounted within-file deletions (`fd6a124`)

`update_index`'s `deleted_symbols` counter only incremented on whole-file
removal. A symbol renamed or removed while its file stays present (the
common case — `replace_file` deletes and reinserts that file's rows) was
silently folded into `new_symbols`, with `deleted_symbols` staying 0 even
though the correct row was already gone from the database.

**Repro**: renaming `Flask.run` → `Flask.run_edited` on a flask copy
reported `new_symbols=1, changed_symbols=1, deleted_symbols=0` while the
database's total symbol count stayed flat (net +1/−1) — the *database* was
already correct, but the JSON report an agent or operator reads was lying
about what happened.

**Fix**: each changed file's pre-edit symbol-id set is diffed against its
freshly parsed set; ids present before and absent after count as deleted.

## Section B — indexing lifecycle integrity (flask, darkreader)

Verified against `.oxide/index.db` row counts directly (`sqlite3`), not
JSON counters alone:

| step | flask (1,629 baseline) | darkreader (993 baseline) |
|------|------------------------|----------------------------|
| clean index | 1,629 symbols/embeddings | 993 symbols/embeddings |
| no-change reindex | identical counts, 0 changed | identical counts, 0 changed |
| single-file edit | 1 changed symbol; unrelated embeddings untouched (1,628/1,629 reused) | 1 changed symbol (992/993 reused) |
| multi-file edit (3 files) | new=1 / changed=2 / deleted=1, total conserved at 1,629 | — |
| rename | old path: 0 rows; new path: rows present; 0 orphan embeddings; total conserved | old path: 0 rows; new path: 4 rows; 0 orphans; total conserved at 993 |
| move to new directory | same as rename, 0 orphans | not run (redundant with rename) |
| delete | 0 rows for deleted path; `files` row gone; total drops to 1,598 | not run |
| recreate same path | rows return; total back to 1,629; **0 duplicate symbol ids** (no `UNIQUE` collision) | not run |

Symbol ids are `FNV1a(file + qualified_name)`, so a rename re-keys every
symbol in that file — the check that mattered most was confirming the old
ids' rows are actually deleted (not orphaned) and that recreating a
deleted path doesn't collide with a stale row. Both held.

## Section C — noise audit

The scanner bug above is the direct answer to section C: before the fix,
"index repository evidence useful for coding context" was violated for any
repo with a non-hidden vendor/venv/build directory containing real source.
After the fix, zero junk paths (`node_modules`, `.git`, `__pycache__`,
`build`, `dist`, `vendor`, `.venv`) appeared in any tested repo's symbol
table. `tailwindcss` (genuinely no supported source files) fails cleanly
with `no_source_files` / `action: stop` — no crash, no empty-but-"current"
index.

## Synthetic hostile-case coverage (not from the repo cache)

- **Nested git repos**: an outer repo containing an inner directory that is
  itself a separate `git init`'d repo. `RepositoryService::discover` from
  inside the inner directory correctly stops at the nearest `.git`, not the
  outer one — matches `git`'s own nearest-repo behavior. The outer index
  does walk into the inner repo's tracked files (its `.git/` itself is
  denylisted/hidden, but its source files are not submodule-excluded) —
  this is a deliberate non-bug: OXIDE does no submodule/nested-repo
  boundary detection, and indexing a present nested repo's code as part of
  the outer working tree is reasonable default behavior for a monorepo-like
  layout.
- **Symlinks**: a symlink to a real directory and a self-referential
  symlink loop. The `ignore` crate's walker does not follow symlinks by
  default, so neither caused a hang or duplicate indexing.
- **Unusual directory names**: spaces, `ünïcödé`, and a leading-dash
  directory name all indexed correctly (UTF-8 paths round-trip through
  SQLite and JSON output unmodified).
- **`.oxide` self-indexing**: never occurs — `.oxide` is dot-prefixed and
  filtered by the walker's hidden-file handling before any denylist logic
  runs.

## Performance spot-check

`django` cold index (2,464 files / 32,978 symbols): 10.8s wall, 206 MB peak
RSS, 0 errors. No-change reindex: 0.68s. Consistent with the synthetic
scaling baseline in `docs/perf-baseline-v0.1.md` (sub-linear RSS growth, no
quadratic cliff observed up to this size).
