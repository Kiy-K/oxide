# SCIP as a code-intelligence provider for OXIDE

**Verdict: rejected as a provider. The Tree-sitter extraction path stays
default.** Gates 1–3 pass; gate 4 fails, and it is the gate that matters most
for OXIDE's design — short-lived CLI invocations, `oxide watch`, a laptop.

Neither `scip-python` nor `scip-typescript` has an incremental mode, so a
single-file edit costs a full re-index: **8.4 s / 571 MB** (Python) and
**12.6 s / 1191 MB** (TypeScript) against OXIDE's **40 ms / 22 MB** and
**140 ms / 37 MB** — 90–210x on exactly the path a filesystem watcher runs on
every save.

The only shape still worth revisiting is an opt-in, out-of-band relation
*overlay*, never on the watch path. It is not free either; see "If this is
ever revisited" below.

## What was measured

`scip-typescript@0.4.0` (published Oct 2025) and `scip-python@0.6.6`
(Sep 2025), installed with one `npm install` — 46 packages, 9 s, 94 MB of
`node_modules`, no Go toolchain. The `scip` CLI (v0.10.0, Sep 2026) is a
separate 26 MB Go binary needed only for `lint`/`stats`. The protocol repo has
moved: `sourcegraph/scip` → **`scip-code/scip`**.

Indexes were decoded against the official `scip.proto` via generated protobuf
bindings, not against documentation prose.

| Subject | Used for |
| --- | --- |
| `fixtures/py_repo`, `fixtures/ts_repo` | relation quality, symbol identity, robustness |
| `swe-agent` (90 Python files, 13.7k lines) | cold + incremental cost, Python |
| `zod` (472 TS files, 98k lines) | cold + incremental cost, TypeScript |

## Gate results

| Gate | Result |
| --- | --- |
| **1. generate Python + TS indexes reliably** | PASS — both index without the project's own dependencies installed, both contain a syntax error to its own file. Caveat: `scip-typescript` hard-fails `no files got indexed` on a `tsconfig.json` it cannot resolve; monorepos need `--pnpm-workspaces`/`--yarn-workspaces`. OXIDE has zero per-repo configuration today. |
| **2. ingest without changing retrieval semantics** | PARTIAL — viable as a relation overlay, not as an extraction provider. See below. |
| **3. materially richer/correcter relations** | PASS — SCIP resolves exact cross-file targets where OXIDE guesses by bare name. |
| **4. incremental indexing practical for `oxide watch`** | **FAIL** — no incremental mode in either indexer (verified in `--help` and by grep over `dist/`). |

### Cost

| | OXIDE cold | SCIP cold | OXIDE 1-file edit | SCIP 1-file edit |
| --- | --- | --- | --- | --- |
| Python (swe-agent) | 0.51 s / 25 MB | 9.14 s / 573 MB | **40 ms / 22 MB** | **8.38 s / 571 MB** |
| TypeScript (zod) | 4.70 s / 52 MB | 12.60 s / 1196 MB | **140 ms / 37 MB** | **12.63 s / 1191 MB** |

`scip-python --target-only <path>` is the only escape hatch — 1.35 s / 199 MB
for one file, still 34x OXIDE, and it emits a document whose `relative_path`
is **empty**, so the output needs repair before it could be ingested.
`scip-typescript` has no equivalent flag.

The `.scip` artifact is comparable in size to OXIDE's whole database (zod:
15.9 MB `.scip` vs 18.5 MB `index.db`, which already holds 4,694 embeddings
plus BM25 postings). It is an additional artifact, not a substitute.

### Relation quality (gate 3)

| | SCIP exact in-project edges | cross-file | OXIDE has a matching bare-name edge | ambiguous for OXIDE |
| --- | --- | --- | --- | --- |
| Python | 55 | 26 | 37 / 55 (67%) | 5 (mean 3.6 candidates) |
| TypeScript | 40 | 17 | 17 / 40 (42%) | 1 (4 candidates) |

SCIP surfaces 18 Python and 23 TypeScript resolved edges Tree-sitter has no
edge for, and disambiguates a handful more. Measured independently, 6 of
OXIDE's own 65 Python `calls` and 1 of its 25 TypeScript `calls` are ambiguous
by bare name — `AuthService.login -> fetch` matches two definitions,
`__init__` fans onto nine.

SCIP also supplies `is_implementation` relationships OXIDE has no equivalent
for: **method-level** override links (`ExponentialBackoff#backoffMs()` →
`RetryPolicy#backoffMs()`) and cross-file base classes with exact targets
rather than bare names.

**SCIP is not a superset.** Tree-sitter found 25 Python call edges SCIP did
not — stdlib/builtin calls (`len`, `int`, `urlopen`, `items`), constructor
calls SCIP types as Type references, and calls through a parameter, which SCIP
demotes to a `local`. In TypeScript, 9 edges in `Button.test.tsx` vanished
because the test dependencies are not installed. Tree-sitter has higher recall
on *"a call happened here"*; SCIP has higher precision on *"of exactly what"*.

### Why gate 2 is only partial

Joining SCIP definitions onto OXIDE symbol rows by `(file, qualified_name)`
matches 85% (Python) and 68% (TypeScript). The misses are systematic and
mostly normalizable — OXIDE's module symbols, `constructor` vs
`<constructor>`, and SCIP field/attribute symbols OXIDE does not model.

What is not fixable: both indexers leave `kind`, `display_name`,
`enclosing_symbol`, `signature_documentation` and `Document.language`
**entirely unpopulated** — 0 of 130 and 0 of 88 fixture symbols, and 0 of
36,527 at zod scale. The only kind signal is the descriptor suffix, and it
collapses what OXIDE distinguishes:

- suffix `type` → OXIDE's `class` **and** `interface`
- suffix `method` → OXIDE's `function` **and** `method`

OXIDE's nine-variant `SymbolKind` cannot be reconstructed from a SCIP index.
That closes off "SCIP as the extraction provider" independently of cost.

## Symbol identity is the integration landmine

SCIP symbol strings embed the package version:

```
scip-python python oxidepy 0.1.0 `oxidepy.auth`/AuthService#login().
```

Bumping the version in `pyproject.toml` / `package.json` changed **130 of 130**
Python and **88 of 88** TypeScript fixture symbol strings — zero survived.

Worse, `scip-python`'s `--project-version` **defaults to the current git
revision**. Confirmed on the swe-agent index: every symbol reads
`scip-python python sweagent b991b389902520f5157c52aebac26bb6b01f146e`. Left
at its default, every commit rewrites every symbol string in the index.

`Symbol::id() = FNV1a(file + \0 + qualified_name)` is persisted and is what
makes incremental re-embedding work (`AGENTS.md`). An id that churned per
commit would invalidate every stored vector on every commit. A raw SCIP symbol
string therefore can never be OXIDE's identity; you would strip scheme and
package and keep only the descriptor path — which works, but discards the
cross-repo identity that is SCIP's actual selling point.

## Robustness and determinism

- **Determinism: byte-identical** across repeated runs for all four indexes,
  at fixture scale and at real-repo scale (90 and 472 files). Caveat:
  `scip-python` resolves against the *ambient* Python environment — it picked
  up the really-installed `requests 2.34.2` — so determinism holds per
  machine, not necessarily across machines.
- **Broken repositories: contained.** A syntax error injected into one file
  left every other file's symbol/occurrence/reference counts identical, and
  the broken file still yielded a partial symbol. Both indexers are
  error-tolerant in the same way Tree-sitter is.
- **Missing dependencies: graceful.** With `axios` absent, `scip-typescript`
  demoted it to `local` symbols while still resolving
  `AuthService.login → ApiClient.request` across files. Intra-project
  cross-file resolution — the only kind OXIDE cares about — survives
  uninstalled third-party packages.
- **Imports.** Neither indexer sets the `SymbolRole.Import` bit at all. Import
  statements appear as ordinary resolved references to the target symbol.
- **Offline.** Both run fully offline at index time after install.

## If this is ever revisited

The only viable shape is an opt-in relation overlay — e.g.
`oxide index --scip <index.scip>`, run manually or in CI, writing resolved
`path#QualifiedName` targets into `symbol_relations` alongside Tree-sitter's
bare names, **never on the `oxide watch` path**, which is what keeps gate 4
out of the loop. Storage needs no change: `symbol_relations(symbol_id, kind,
target)` already has the right shape.

It is still not free. `RelationGraph::callers_of`/`implementors_of` read those
targets, so changing them from bare names to qualified ids changes what
`context.rs`'s bounded expansion returns — a retrieval-semantics change,
requiring `tests/benchmark_gate.rs` plus a `docs/canonical-baseline.md`
comparison with any diff explained rather than assumed benign.

The gain also lands narrowly. `context.rs` already filters `callers_of` down
to the seed pool's own files, so SCIP's cross-file exact edges do not lift the
ceiling documented in `docs/retrieval-coordinator/README.md` — a caller in a
file the seed search never surfaced stays invisible either way. What SCIP buys
is precision *inside* that scope: the 5 Python and 1 TypeScript edges that
currently fan onto as many as nine candidates.

## Harness bugs found and fixed

Recorded because a harness that is wrong in the candidate's favour — or
against it — is worse than no harness. All three were found before any
conclusion was drawn.

1. **`tsconfig.json` clobbered.** Staging `zod/packages/zod` as a standalone
   subject copied the monorepo-root `tsconfig.json` over the package's own,
   producing a spurious `scip-typescript` hard failure. Re-run on the whole
   repo with the documented workspace handling; it indexed 466 documents. The
   underlying fragility is real and recorded above, but the first failure was
   the harness's fault.
2. **SCIP descriptor parser mis-parsed methods.** The grammar is
   `name(disambiguator).` — the trailing `.` is part of the method suffix.
   Missing it made every callable parse fail and reported *zero* SCIP call
   edges, which was obviously wrong rather than quietly wrong.
3. **Parameter references credited to their owning method** (caught by a Codex
   review of the finished harness). `qname()` drops the final descriptor, so a
   reference to `login().(session_id)` resolved to `AuthService.login`. This
   inflated SCIP's edge counts by **97 Python** and **34 TypeScript** edges.
   After the fix, in-project resolved edges dropped 58 → **55** (Python) and
   48 → **40** (TypeScript). Direction of the finding unchanged; the corrected
   figures are the ones quoted above.

The same review raised three further concerns that measured to zero effect on
this data, so the numbers stand — but all three must be fixed before any of
this becomes a real ingester:

- the parser omits SCIP's `!` macro suffix and doubled-backtick escapes
  (**0 occurrences** across all four indexes);
- caller attribution is line-granular rather than position-granular
  (**0 same-width ties** in either fixture, measured by `attrib_risk.py`);
- dictionary keying could hide duplicate rows (**0 collisions** on either
  side).

## Reproducing

```bash
npm install @sourcegraph/scip-typescript @sourcegraph/scip-python
protoc --python_out=. scip.proto        # from github.com/scip-code/scip

# generate
(cd <py-repo> && scip-python index . --project-name=<name>)
(cd <ts-repo> && scip-typescript index)          # add --pnpm-workspaces for monorepos

# OXIDE baseline
OXIDE_EMBED_NATIVE=hashed oxide index <repo>

# compare (harness/ in this directory)
python3 harness/derive.py  <index.scip> edges.json
python3 harness/refcmp2.py <repo>/.oxide/index.db edges.json LABEL harness/
python3 harness/ingest2.py <repo>/.oxide/index.db edges.json LABEL harness/
```

`harness/occ.py` dumps occurrence roles, populated-field counts and kind
histograms; `harness/attrib_risk.py` quantifies the attribution concern above.
