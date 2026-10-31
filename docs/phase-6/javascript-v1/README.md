# JavaScript / JSX milestone evidence

Implemented source semantics and limits: [../../javascript.md](../../javascript.md).
This adds a language to the existing engine; retrieval, routing, packing and MCP
policy are unchanged. `oxide_search` remains the sole public MCP tool, with search
and context modes. The [structural MCP trial](../structural-mcp-trial/README.md)
remains REJECT; its preregistration, freeze, deviations and results are untouched.
Public release remains deferred.

## Grammar decision

Selected **tree-sitter-javascript 0.23.1**, ABI 14, with the unchanged locked
**tree-sitter 0.26.0** (ABI 13–15). The initially evaluated 0.25.0 grammar is ABI
compatible but rejects common JSX `<div class="app"/>` and `<label for="id"/>`.
On Preact it made 18 of 202 JavaScript files partial; 0.23.1 parses all 202 completely.
`grammar-selection.json` preserves both candidates' native probes, the rejected
result and the published wheel list. `grammar-probe.py` reproduces the probes in
an environment containing either pin. Conformance includes these attributes plus
import attributes, static blocks, private fields, optional chaining and JSX.

The selected dependency publishes cp39-abi3 wheels for Linux x86_64, Windows
x86_64 and both macOS architectures. Fresh artifact installations and JS/TS
conformance passed on Linux x86_64 with Python 3.12.15, 3.13.16 and 3.14.8. Windows
and macOS runtime verification of this milestone is pending hosted CI; their code
branches passed local strict mypy. Wheel availability and static checking are not
native OS execution. No Node or Bun runtime dependency was introduced.

## Correctness and real repositories

`fixtures/js_repo` covers ESM barrels, named/default exports, classes, fields,
JSX, CJS namespace/destructured imports, static exports, TS callers and JSX tests. JS/TS bridges reject merged TS modules and directory-only
loader specifiers instead of inventing an exact source target.
`test_javascript.py` adds adversarial coverage for shadowing, opaque mutations,
package type, collisions, default-property interop, Unicode identities, opaque
callback/block bindings, object-method lexical isolation, partial
syntax, invalid UTF-8, damaged facts, dialect isolation and parser depth/long-chain
reliability. No graph target is invented for unsupported export shapes. Unmodeled callback,
block and class-block bindings suppress uncertain lookup, and object methods never
become bare lexical exports. This intentionally loses some valid local calls.

Real checkouts were fetched once into private temporary directories and pinned:

| Repository | Commit | JS/JSX complete / total | JS symbols | Entities / relations (all languages) |
| --- | --- | ---: | ---: | ---: |
| preactjs/preact | `630a87e468fc14eca5a5fd617288b4a606763d9d` | 202 / 202 | 2,996 | 4,239 / 28,556 |
| expressjs/express | `0dee2c52c7c873498f8ed7d8ee4ddd0a04d3ada8` | 141 / 141 | 156 | 512 / 9,932 |

Preact's JS symbols: 1,241 functions, 1,183 methods, 517 classes, 55 exported
variables. Express includes functions exported directly through CommonJS.
Syntactic coverage is not name-resolution coverage. Across all parsed sources in
each checkout, Preact has 539 resolved / 6 ambiguous / 944 unresolved imports and
1,660 / 1 / 16,766 calls. Express has 45 / 0 / 317 imports and 331 / 0 / 8,440
calls. Packages, instance methods, export-object aliases and dynamic values remain
unresolved by design. In particular Express's `exports = module.exports = …`
idiom is opaque; this milestone does not claim complete Express module resolution.

The first 20 resolved import examples from each checkout were inspected against
source/target files; 10 deterministic SHA-selected cross-file call examples from
each were also inspected. The call samples, signatures and source expressions are
retained under `real/`. No contradiction was found in these samples. This is a
bounded spot check, not a global precision estimate or compiler oracle.

## Incremental correctness

Both real checkouts produced byte-equivalent logical derivations with all facts
reused. A private copy of each capture was indexed, one JS source edited, and every
persisted file/entity/relation compared with a separate fresh rebuild. Equality
held; Preact reused 242/243 parsed files, Express 140/141. `real/*.json` records
snapshots, graph fingerprints, counts, timings and parity assertions.

Fixture tests also compare persisted incremental/full builds through addition,
CJS export mutation, deletion, restoration and JSX-to-TSX moves. A metadata-only
`package.json` type change reuses both source facts yet changes resolution correctly.
Cache encoding round-trips direct CJS exports including Unicode identifiers;
damaged entries are parsed again. Existing persistence/recovery tests remain gates.

## Indexing, latency and memory

Three fresh processes per repository using the existing `scripts/bench.py`, three
warm query repeats, budget 1,024; each scope has CPUQuota=200%, MemoryMax=3G,
MemorySwapMax=0. Linux x86_64, CPython 3.13.16, pinned native dependencies and FTS.
These laptop timings are diagnostic; other verification could overlap. They are
not a performance regression gate or a speedup claim.

| Repository | Cold index median | Unchanged median | Warm search / context median | Peak RSS median |
| --- | ---: | ---: | ---: | ---: |
| Preact | 1.173 s | 0.0205 s | 0.00400 / 0.0562 s | 171.7 MiB |
| Express | 0.450 s | 0.0146 s | 0.00165 / 0.0166 s | 147.4 MiB |

One-run parse/resolve and reused-facts derivation: Preact 0.515 / 0.157 s; Express
0.178 / 0.035 s. Persisted single-file edit: 0.746 / 0.266 s. The separate parity
runner retains facts and opens two stores concurrently, peaking at 293 / 221 MiB;
it measures a different workload than the single-store benchmark above.
Raw distributions, environment, queries and all timings are in `real/`.

## Bounded revisit of excluded ContextBench JavaScript

Historical TypeScript tasks, audits, corpora and results retain their original
interpretation. `excluded-js-inventory.json` inventories their 280 JS spans across
38 mixed tasks. This diagnostic selected the first three MUI task IDs with JS gold
before scoring: 11858, 11987, 12236. It does not evaluate all 280 spans or the 36
JS-only/other-language MUI tasks omitted from the historical TS task set.

Each original pinned base commit was captured into a new corpus containing a `git archive` projection of
JS/JSX/MJS/CJS and TS/TSX/MTS/CTS sources, package.json and tsconfig files. The cost
cap is 7,500 source/config files (actual counts 2,376, 2,388, 6,686), separate from
the historical TS-only cap. No historical corpus was expanded in place.

The same ContextBench budgets (256/1,024/4,096), scorer and integrity rules were
used, with `contextbench_js.py` projecting module-level lines independently through
the native JS grammar. The SHA-checked original full.parquet and original gold
content hashes anchor the audit. Gold is never moved to better lines. Of 24 JS
spans, 20 are exact and 2 contained; 1 drift and 1 wrong-range span are retained
separately and excluded from verified metrics. No TS spans enter these denominators.

Both arms use the same corpora, queries, gold and selected 0.23.1 scorer grammar.
Before is the installed wheel from `95090a2` (no JS understanding); after is this
implementation. No retrieval parameters or routing budgets were tuned.

| Verified macro metric (3 tasks, 22 spans, 151 lines) | Before | After |
| --- | ---: | ---: |
| entry recall@20 | 0.0556 | 0.3889 |
| routed coverage | 0.0556 | 0.4444 |
| complete span recall@1,024 | 0 | 0.2222 |
| complete span recall@4,096 | 0 | 0.3889 |
| line recall@4,096 | 0 | 0.4211 |

At 4,096 units, seven verified spans are complete after (zero before); 14 remain
lost at entry and one at budget. All bundles are deterministic and within budget.
Cost increases with understanding more source: index times are 0.50–1.03 s before,
2.81–4.33 s after; peak RSS 173 → 261 MiB over the three-task process.

This is a small diagnostic gain on previously excluded evidence, not a new held-out
quality claim, a replacement TS benchmark, or evidence to reopen structural MCP
research. `context-before/` and `context-after/` retain results, integrity audits,
corpus snapshots/commits, metrics and performance. No paid agent trial was repeated.

## Existing baselines and verification

All 44 Python tasks and 89 TypeScript tasks retain identical metrics, entries,
routes, payload hashes, gold and loss classifications. The only output difference
is the derivation hash naming the new JavaScript components. Historical
`baseline-v8` / `typescript-v5` remain untouched; `baseline-v9` / `typescript-v6`
are explicit identity-only successors, with the same tasks, corpora, scorers,
tolerances and assertions. No frozen artifact was changed to hide a regression.

`verification.json` records the completed canonical suite, full TS gate, fresh
wheel/sdist checks, platform limits and preservation checks. `freeze.txt` identifies
the implementation/test/evaluation inputs and retained evidence. Temporary raw
logs and fresh environments are under `/tmp/oxide-js/`; only new milestone evidence
is retained here. A temporary result-recorder teardown error was corrected and the
checks rerun; it did not indicate an engine failure.

## Reproduction

From a locked checkout, use private data and the resource limits above:

```sh
mise run verify
mise run contextbench-ts
uv run python scripts/eval_javascript.py real /path/to/preact /tmp/preact.json
uv run python scripts/eval_javascript.py real /path/to/express /tmp/express.json
uv run python scripts/bench.py /path/to/preact --iterations 3 --warm 3 --query createElement --query render
uv run python scripts/bench.py /path/to/express --iterations 3 --warm 3 --query createApplication --query render
uv run --with pyarrow python scripts/eval_javascript.py rows /path/to/full.parquet /tmp/js-rows.json
uv run python scripts/eval_javascript.py fetch /tmp/js-rows.json /tmp/js-corpora
uv run python scripts/eval_javascript.py context /tmp/js-rows.json /tmp/js-corpora /tmp/js-context
```

`real` edits only its own private copy, never the supplied checkout. The before arm
requires the previous milestone wheel installed in a fresh environment; run the
same scorer script with that environment's Python, outside the checkout and with
`-I`, and install the selected grammar for the scorer. The native grammar is not
used by that old engine. Artifact smoke instructions remain in
[../../platforms.md](../../platforms.md).

Next language priority: Go, whose explicit package/import structure fits a bounded
source resolver; then Rust, with macros and conditional compilation explicitly
limited until supported by evidence. Do not broaden retrieval or structural MCP
research without new evidence.
