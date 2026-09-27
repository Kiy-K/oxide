# CodeGraph Kernel as an OXIDE extraction engine — differential evaluation (#23)

Research only. Nothing here is wired into production indexing; no
`ExtractionEngine` trait was added. Parent roadmaps: #27, #9.

**Disposition: REJECT** — see [§12](#12-disposition-reject).

## 1. Pinned revisions

| what | revision |
|---|---|
| OXIDE starting `main` | `87b97f354bccb48fda7a9d430cb76cd11d83c49d` (== `origin/main` at start) |
| CodeGraph | `colbymchenry/codegraph` tag **v1.6.0** = `dfccdf62547fcd76d343344d823a0e1998d3a89f`; `codegraph-kernel` crate `0.1.0`, kernel ABI 2 |
| Shipped reference binary | npm `@colbymchenry/codegraph-linux-x64@1.6.0`, `lib/kernel/codegraph-kernel.node` |
| Churn check | CodeGraph `main` HEAD `ba3c21e50d9129d2f5f3843ec3728868ae6d47a1` (2026-09-16, unreleased) |
| Toolchain / machine | `results/environment.txt` (rustc 1.98.0, i7-13620H, Linux 7.2.5) |

v1.6.0 was chosen over HEAD because it is the released artifact users run,
and because it lets the harness be validated against the shipped binary
(§3). HEAD was measured separately as a churn data point (§9).

## 2. What each extractor actually does (Phase 1)

### OXIDE — the `index/pipeline.rs` seam

`parser::parse_file(file, src, lang)` → `parse_file_with`:

1. `collect_imports` — parse #1, `collect_meta` walk: raw import module
   strings from **anywhere** in the file, sorted + deduped. C/C++ quoted
   includes become `./x.h`; Rust `use` groups are kept verbatim.
2. `TagsExtractor::extract` — parse #2 (`collect_meta` again: export ranges,
   decorator ranges, Go receivers/interfaces, Java/C++ parameter
   signatures, Ruby singletons, C/C++ prototype + body spans, Rust `impl`
   stand-ins, C++ scopes) and parse #3 inside `tree-sitter-tags`
   (`*_tags.scm` + locals). Flat tags → byte-range containment stack →
   `qualified_name` (`.`-joined, Java/C++ `(sig)` suffix, Go `Recv.m`,
   Ruby `self.`), kind mapping onto 9 kinds, Python method/function split,
   decorator-inclusive start line, export flag, line-based `content_hash`.
3. First-wins dedup on `qualified_name` (load-bearing: ids are
   `FNV1a(file + \0 + qualified_name)`), stand-ins dropped when the real
   declaration exists, synthetic `<file>:__module__` symbol appended.

`structural_relations::compute_file_relations(&symbols, src, lang)`:
parse #4 (`*_callers.scm` → `(line, bare callee)`), parse #5
(`*_implementors.scm` → `(line, class, bare base)`), attribution to the
innermost enclosing symbol (module fallback), per-symbol sorted/deduped
`calls`/`bases`. Constructions (`new X`, `X.new`), Rust macros and JSX
components count as calls; lowercase JSX intrinsics do not.

Unsupported/degraded: never errors — a file with ERROR nodes still yields
whatever tags survive, plus the module symbol. Markdown yields only the
module symbol.

### CodeGraph Kernel — `codegraph-kernel/src` (Rust, ~25k LOC)

A napi-rs **`cdylib`** (`publish = false`, not on crates.io) whose single
extraction entry point is `extract_file(file_path, content, language) ->
Result<{meta,nodes,edges,refs,arena}>`: one tree-sitter parse, then a
hand-written per-language walker that is a bug-for-bug port of CodeGraph's
TypeScript extractor. Output is five flat little-endian buffers (ABI v2,
`buffers.rs`): nodes (23 kinds, name, `::`-joined qualified name, 1-based
lines, UTF-16 columns, docstring, signature, flags), `contains` edges, and
unresolved refs (`calls`, `instantiates`, `extends`, `implements`,
`imports`, `references`, `decorates`, `function_ref`) carrying the written
target text. Grammars are pinned to CodeGraph's wasm parity contract
(tree-sitter **0.25**; python 0.23, go 0.23, the JS grammar for JS; four
vendored C grammars incl. a 35 MB Scala `parser.c`).

Failure behavior is **all-or-nothing by design**: every walker returns
`Err("defer: parse tree contains errors — wasm recovery is canonical")`
when the tree has any ERROR node (and on stack exhaustion). In CodeGraph
the TypeScript/wasm extractor then handles the file. C/C++ also depend on
TS-side `preParse` macro blanking (`src/extraction/languages/c-cpp.ts`)
applied *before* the kernel call.

### Mapping

| OXIDE concept | CodeGraph concept | comparable? | normalization | unavailable |
|---|---|---|---|---|
| `Symbol` (non-module) | node, kind ∉ {file, import, export, parameter} | yes | N1 kind classes, N2 matching | CG-only kinds: variable, field, property, enum_member, route, component |
| synthetic `__module__` | `file` node | no (both stand-ins) | used only as top-level attribution owner | — |
| `kind` (9) | `kind` (23) | class-level | N1 | — |
| `start_line`/`end_line` | `startLine`/`endLine` | yes | tiered (N2) | OXIDE has no columns |
| `qualified_name` (`.`, sig suffix) | `qualifiedName` (`::`, package/namespace prefix, no sig) | partly | N3, N4 | — |
| `parent` | `contains` edge | implied by qn | not scored separately | — |
| `imports` (module strings, anywhere) | `import` nodes (+ `imports` refs) | yes | N6, N6b, N6c, N6d | CG skips function-scoped imports |
| `calls` (bare, per enclosing symbol) | `calls` + `instantiates` refs (written text, per source node) | yes | N5, N5b, N7 | — |
| `bases` | `extends` + `implements` refs | yes | N5 | — |
| `exported` | `isExported` flag | not scored | — | — |
| `signature` (first line) | `signature` (params) | no (different meaning) | — | — |
| `content_hash`, `references` | — | out of scope (OXIDE cross-file/indexer) | — | — |
| — | docstring, decorators, typeParameters, returnType, `references`/`decorates`/`function_ref` refs | no | reported as CG-extra only | OXIDE schema has no slot |

## 3. Harness

```
docs/codegraph-kernel-eval/
  harness/
    setup.sh            fetch CodeGraph @v1.6.0 into cgk-native/vendor/ and 3 corpus repos (gitignored)
    build_corpus.py     manifest; languages via OXIDE's own scanner::language_for_source
    cgk-native/         standalone crate: kernel source UNMODIFIED via #[path];
                        src/lib.rs replaces only the napi entry point; decode.rs = buffers -> structs
    shipped_digest.js   digests the npm-shipped .node for the fidelity check
    preparse.js         applies CodeGraph's own shipped C/C++ preParse (sensitivity run)
    compare.py          normalization + diff (all rules N1–N8 live in this file)
    summarize.py        tables
    run.sh              end-to-end: fidelity, 3x determinism, diffs, pinned benches, RSS
  results/              preserved raw evidence (dumps gzipped, bench JSON, diffs, digests)
examples/extraction_differential.rs   OXIDE side: dump | bench | classify
```

**Fidelity check:** the shim's raw buffers are **byte-identical to the
shipped v1.6.0 `codegraph-kernel.node` on all 688 files** it was given
(same bytes for every extracted file, same `defer:` message for every
refused one — `results/kernel_digest.tsv`, `cmp` in `run.sh`). The measured
kernel is the real kernel, minus only the N-API crossing.

Reproduce: `harness/setup.sh && harness/run.sh 10`;
`python3 harness/summarize.py results` regenerates `results/summary.md`.

## 4. Normalization (explicit; nothing else is applied)

- **N1 kind classes:** struct/union→class, trait/protocol→interface,
  namespace→module; others identity. Agreement is class-level only.
- **N2 symbol matching:** one-to-one greedy on bare name; tiers T1 same
  start+end, T2 same end, T3 same start, T4 overlapping; ties prefer equal
  kind class, then smallest line distance. "Exact span" = T1.
- **N3 qualified names:** CG `::`→`.`; OXIDE's `(params)…` suffix stripped.
- **N4:** a qn that is a dotted suffix of the other is "suffix" (Java
  package / PHP namespace prefixes CG adds), not "differs".
- **N5 relation targets:** last segment after `.`/`::`/`->`/`\`, generics
  and parens stripped (CG records written receiver paths: `strings.Join`).
- **N5b:** CG targets that do not reduce to an identifier (raw expression
  text, IIFEs, `func` literals) are counted apart (81), not as CG-only.
- **N6 imports:** quotes/`<>` stripped. **N6b** C/C++ leading `./` (OXIDE's
  relative-include marker) stripped. **N6c** Rust: CG `use` nodes are named
  by first segment only, so CG's full-path `imports` refs are used.
  **N6d** OXIDE's grouped Rust `use` trees are expanded to leaf paths.
- **N7:** OXIDE counts constructions as calls; CG emits `instantiates`.
  Calls compare against CG `calls ∪ instantiates`; CG-only residue is split
  by source kind.
- **N8:** unmatched CG `namespace` nodes (Java `package`) reported apart.
- Attribution: a CG ref's source row maps to its matched OXIDE symbol; the
  file node maps to OXIDE's module symbol; an unmatched source row keeps a
  distinct owner key (so it can only count as a difference).

## 5. Corpus (689 files, `results/manifest.jsonl`)

| group | files | languages |
|---|--:|---|
| `fixtures/conformance/*` (all files) | 46 | all 11 OXIDE languages |
| `tests/precomputed_relations_conformance.rs` inline sources | 14 | py, ts, tsx |
| `fixtures/py_repo`, `fixtures/ts_repo` | 17 | py, ts, tsx, 1 markdown |
| flask @7ee9ceb71e86 (`src`,`tests`) | 62 | Python |
| darkreader @a787eb511f45 (`src`) | 84 | TS + TSX |
| axios @0abc70564746 (`lib`) | 54 | JavaScript |
| tokio @43c224ff47e4 (`tokio/src`) | 96 | Rust |
| gin @dcaa4296d111 | 99 | Go |
| gson @8b4b55051489 (`gson/src/main/java`) | 86 | Java |
| zstd @823a28a1f4cb (`lib`) | 61 | C |
| fmt 11.1.4 (`include`,`src`,`test`) | 70 | C++ |

Real repos: sorted candidate list, every k-th file, cap 120, no size /
content / outcome filter. They are the repos OXIDE's language-support doc
already benchmarks (gin and gson pinned there; fmt added for C++). **Bias
notes:** the conformance fixtures were written for OXIDE's semantics and
include one deliberately broken file per language; Ruby and PHP have
fixtures only (11 files, no real repo) and are reported but not relied on;
CodeGraph's 8 extra languages (C#, Swift, Kotlin, Scala, Dart, R, Lua,
Luau) have no OXIDE counterpart and were not differential-tested.
Markdown is kept (OXIDE-only; CG "unsupported").

## 6. Output differences by language (raw input)

Files the kernel extracted (594 of 689):

| lang | OXIDE syms | matched | exact span | kind agree | qn exact / suffix / differs | OXIDE-only | CG-only (comparable / schema-extra) | calls exact / OXIDE | bases exact / OXIDE | imports exact / OXIDE |
|---|--:|--:|--:|--:|---|--:|---|---|---|---|
| c (15 files) | 188 | 142 | 142 | 132 | 142/0/0 | 46 | 8 / 1 | 269/272 | 0/1 | 43/43 |
| cpp (38 files) | 306 | 245 | 245 | 192 | 236/0/9 | 61 | 18 / 20 | 752/761 | 4/4 | 144/144 |
| go | 1989 | 1724 | 1643 | 1630 | 1660/64/0 | 265 | 1 / 3 | 5971/5977 | 10/29 | 520/520 |
| java | 1021 | 966 | 966 | 965 | 0/923/43 | 55 | 218 / 235 (+90 packages) | 2165/2434 | 68/72 | 694/694 |
| javascript | 273 | 230 | 230 | 230 | 221/3/6 | 43 | 54 / 0 | 529/680 | 0/4 | 137/138 |
| python | 1646 | 1646 | 1072 | 1544 | 1646/0/0 | 0 | 21 / 6 | 2557/3074 | 123/123 | 370/409 |
| rust | 1262 | 1151 | 1151 | 1148 | 989/156/6 | 111 | 25 / 92 | 2491/2769 | 205/247 | 718/777 |
| tsx | 184 | 178 | 178 | 178 | 178/0/0 | 6 | 7 / 2 | 290/410 | 2/2 | 217/217 |
| typescript | 449 | 382 | 382 | 382 | 382/0/0 | 67 | 59 / 44 | 992/1129 | 14/14 | 137/143 |
| ruby + php (fixtures) | 43 | 41 | 41 | 36 | 19/20/2 | 2 | 1 / 2 | 13/14 | 8/8 | 7/7 |
| **total** | **7361** | **6705 (91.1%)** | **6050 (82.2%)** | **6437 (96.0% of matched)** | 5473/1166/66 | 656 | 412 / 405 | **16029/17520 (91.5%)** | **434/504 (86.1%)** | **2987/3092 (96.6%)** |

"qn exact" is measured **after** N3 strips OXIDE's signature suffix; on
the raw strings 5256 match (C++ drops from 236 to 19), so it measures
declaration correspondence, not symbol-id compatibility. Go's 81 T4
(overlap) matches and 64 "suffix" names are mostly OXIDE's grouped-
declaration defect below, which the lenient tiers absorb rather than flag.

Callee-name sets (attribution-free): 7994/8520 (93.8%). CG-only call
pairs: 644 from `calls`, 803 from `instantiates` (464 of them Go composite
literals, which OXIDE deliberately does not count).

**What the residue is** (checked against examples, `results/diff.json`):

- *Documented OXIDE policy, not CG error:* decorator-inclusive spans
  (Python T2 = 574), JSX components as calls (TSX), Rust macros as calls,
  construction attributed to the class (Ruby `X.new`), first-wins dedup of
  same-qn Rust impl methods, imports collected anywhere (CG skips
  function-scoped Python imports: 39), Java/C++ signature-bearing names.
- *Taxonomy differences:* Go `type X string` class↔type_alias (36);
  module-level Python/Go/Ruby constants↔`variable` (163); C++ in-class
  method declarations `function`↔`method` (53); CG has no Rust `mod` / C++
  namespace nodes (88); CG emits `const` values, fields and properties
  OXIDE's schema has no slot for (schema-extra).
- *OXIDE defects surfaced (independent reason: `go_tags.scm`'s own comment
  says package-level, and Go grouped declarations are siblings):*
  (a) 230 of the 234 Go OXIDE-only "constants" are declared **inside
  function/method bodies** (221 `var`, 9 `const`; e.g. `TestX.obj`) because
  the `var_declaration`/`const_declaration` patterns are not anchored to
  `source_file`; the other 4 are top-level `var`s. (b) Grouped
  `const (…)`/`var (…)` blocks are tagged with the whole group's range, so
  the containment stack nests each entry under the first sibling:
  `MIMEJSON.MIMEHTML.MIMEXML…` in gin's `binding/binding.go` — 75 of 393
  Go constants carry such a nested `qualified_name`, which is id-bearing.
  Both are out of scope here; candidates for #26.
- *CG gaps:* Go interface embedding produces no relation (8/8 OXIDE bases
  missing); raw expression text recorded as relation targets (81); v1.6.0
  misses Java enum-constant-body methods (most of Java's 54 OXIDE-only
  methods; HEAD recovers them, §9). Java CG-only output is mostly
  `static final` fields as constants (166), anonymous classes and their
  members.

No CG-only output here is claimed "better": nothing in this run
establishes that the extra nodes/refs improve OXIDE's retrieval.

### C/C++ sensitivity: CodeGraph's own `preParse` applied first

| | C files refused | C++ files refused | OXIDE symbols on refused files |
|---|--:|--:|--:|
| raw input (what a Rust-only integration sees) | 50/65 (77%) | 35/73 (48%) | 9,183 |
| after CodeGraph's shipped TS `preParse` | 25/65 (38%) | 28/73 (38%) | 8,353 |

Every refused file is one whose tree has an ERROR node (`cg_failed ==
oxide_tree_has_error` for every language). Outside C/C++, the only refused
files are the nine deliberately broken conformance fixtures, where OXIDE
still returns partial symbols. Full tables: `results/summary.md`.

## 7. Performance

Same machine, both pinned to one P-core (`taskset -c 2`), same codegen
profile (thin LTO, `codegen-units = 1`), 3 fresh processes × 10 reps per
engine, per-file median over the 27 warm samples. OXIDE = `parse_file +
compute_file_relations`; CG = `extract_file + decode` (decode is 1% of it).
Only the 594 files the kernel extracted are compared.

| | OXIDE | CG kernel |
|---|--:|--:|
| total (594 files, 3.2 MiB) | 2,184 ms | 486 ms |
| throughput | 1.5 MiB/s | 6.6 MiB/s |
| OXIDE/CG, per-language totals | 2.9× (ruby) – 5.0× (c, go) | |
| OXIDE/CG per file, p10 / median / p90 | 2.99 / 4.01 / 4.78 | |
| cost in bare-parse equivalents (same file, own grammar) | **7.5** | **1.5** |
| cold first rep vs warm median (whole corpus) | 9,223 vs 8,830 ms (+4.4%, lazy query compile) | 1,331 vs 1,327 ms |
| peak RSS, one full dump (`/usr/bin/time`) | 39.5 MiB | 34.7 MiB |
| VmHWM growth over corpus-load baseline | +27.1 MiB | +24.4 MiB |

On this workload (OXIDE's seam vs kernel extraction + generic decode, the
594 files the kernel accepts) the kernel is ~4.5× faster and deterministic.
The RSS rows come from full-corpus runs that include the kernel's refused
files and the harness's attribution parse, so "similar memory" holds for
this harness only, not for an integrated indexer. OXIDE's seam performs
**five full parses** per file (§2) and costs 7.5 parse-equivalents against
the kernel's 1.5. That ratio suggests much of the gap is repeated parsing
— #24's hypothesis, fixable without a new engine — but it is a ratio, not
a measurement of what a one-parse OXIDE would achieve; neither that nor
the query-vs-walker residue was measured.

The 94 refused files cost OXIDE 6.6 s to extract and the kernel 0.84 s to
*refuse* (it parses, then discards).

## 8. Memory, build and dependency cost

| | |
|---|---|
| kernel cold release build (`-j 2`, clean target) | 44 s wall / 68 s user, 43 crates, 471 MiB peak compiler RSS (`results/cgk_build_cold.log`) |
| kernel binary (kernel's own fat-LTO, stripped profile) | 35.7 MB (shipped `.node`: 35.6 MB); 31.4 MB of it grammar tables (`.rodata`) |
| OXIDE release binary today | 58.0 MB |
| grammar archives OXIDE does not already link at the same revision | ≈ 23.6 MB (C#, Swift, Scala, Kotlin, Dart, R, Lua/Luau, plus python 0.23 / go 0.23 / JS duplicates) — an estimate from archive sizes, not a measured link |
| tree-sitter core | kernel 0.25 vs OXIDE 0.27; `tree-sitter` declares `links = "tree-sitter"`, so one binary can hold only one |
| porting the kernel to 0.27 | `cargo check`: **390 type errors in 17 of the kernel's files** (child-index API `usize`→`u32`; `results/ts027_port_check.txt`) — mechanical, but a permanent fork of upstream's walkers |

## 9. Determinism and failure behavior

- **Determinism:** 3 separate processes per engine produced byte-identical
  dumps (`results/determinism.sha256`) — both engines pass.
- **Failure:** OXIDE never fails a file; the kernel refuses whole files on
  any ERROR node (§6), with no partial output. CodeGraph's fallback is its
  TypeScript/wasm extractor; a Rust-only OXIDE has no equivalent, so every
  refused file would need OXIDE's current extractor as fallback — two
  extractors with different semantics, chosen per file by whether the tree
  has an error.
- **Upstream churn:** 220 of 689 files (32%) produce different kernel output
  at HEAD `ba3c21e` than at v1.6.0, three weeks apart (+374 nodes, +955
  refs; agreement with OXIDE 6705→6803 symbols, 16029→16292 calls;
  `results/diff_head.json`). In 64 of those files node identity/span
  tuples changed — what would move OXIDE symbol ids or content hashes if
  the kernel were the source of truth; the other 156 differ only in refs,
  which would change stored relations but not ids or embeddings (`calls`
  are not part of the embedding text).

## 10. Engineering fit

| criterion | finding |
|---|---|
| Rust-native integration | Possible: the unmodified source compiles as a plain lib with a ~90-line shim, and its output is byte-identical to the shipped binary. But the crate is a `publish = false` napi `cdylib`, so it must be vendored (~25k LOC Rust + vendored C grammars) or forked. |
| API / ownership friction | Low: `(&str, &str, &str) -> Result<owned buffers, String>`, no lifetimes; decoding costs ~1%. |
| Language coverage | Kernel 20 vs OXIDE 11 (+ markdown). The 8 extra languages are unevaluated here, and #27 says broader coverage is insufficient by itself. |
| Tree-sitter/parser duplication | Core version conflict (above). Grammar revisions are pinned to CodeGraph's wasm parity gates (python 0.23 vs OXIDE 0.25, go 0.23 vs 0.25, JS grammar vs OXIDE's deliberate TSX-for-JS invariant). Aligning them breaks upstream's contract; not aligning ships two grammars per language. |
| Error behavior | Whole-file refusal (above); C/C++ additionally needs CodeGraph's TS `preParse` ported just to get down to 38% refusal. |
| Keeping OXIDE semantics | Not drop-in. `qualified_name` (feeds persisted symbol ids) differs for Java, C++, PHP and Rust-impl cases; spans (feed `content_hash`) differ for decorated definitions. Adopting the kernel's output forces a full re-embed and rewrites all 11 conformance goldens; preserving OXIDE's contract instead needs an adapter that re-derives decorator widening, Java/C++ signatures, Go receivers, Ruby singletons, stand-in rules and the JSX/macro/construction call policy — `tags.rs` again, on top of a second walker. |
| Scanner / discovery / incremental / storage | Could stay unchanged in principle (the seam is a pure per-file function), but only if the output contract above were matched. |

## 11. Independent review (Codex)

Read-only review of the harness, results and this report (fresh Codex
thread). Verdict: **REJECT supported**; no production change (`git status`
/ `git diff` clean for tracked files); summary figures match
`results/summary.md` and `results/diff.json` except as noted. Findings and
resolutions:

| # | severity | finding | resolution |
|---|---|---|---|
| 1 | MAJOR | N2's overlap tier and N4's suffix rule absorb Go grouped-declaration names that OXIDE nests under a sibling, hiding an id-bearing defect | Confirmed and sharpened: 75/393 Go constants nested (`MIMEJSON.MIMEHTML…`); documented as OXIDE defect (b) in §6 and as a caveat on the agreement numbers |
| 2 | MAJOR | "230 of 234 are function-local `var`s" mis-states composition | Confirmed: 230 are inside function/method bodies = 221 `var` + 9 `const`; 4 top-level `var`s. §6 corrected |
| 3 | MAJOR | Churn: "every change would move ids/hashes" overstated | Confirmed: 64 files change node identity/spans, 156 only refs. §9 corrected |
| 4 | MINOR | qn "exact" depends on N3 signature stripping | Confirmed: 5473 after stripping vs 5256 raw (C++ 236→19). Caveat added in §6 |
| 5 | MINOR | Speed/RSS measure a narrower workload; parse-equivalents are a ratio, not a measured one-parse result | Wording narrowed in §7 and §12 |
| — | note | Build-cost figures had no raw record | Added `results/cgk_build_cold.log` and `results/ts027_port_check.txt` |

None of the findings changes a failed gate.

## 12. Disposition: REJECT

| gate | result |
|---|---|
| Completeness / failure behavior | **Fail.** 77% of C and 48% of C++ files refused on raw input (38% / 38% even with CodeGraph's own TS pre-pass); no partial recovery; the canonical fallback is JavaScript. |
| Compatibility with OXIDE's extraction contract | **Fail.** 91% symbol / 91.5% call agreement on commonly extracted files, but the differences sit in id- and hash-bearing fields; adoption means a forced re-embed or an adapter that re-implements OXIDE's normalization. |
| Build / integration cost | **Fail.** tree-sitter `links` conflict requiring a 390-error fork, unpublished napi crate, duplicated grammars (≈ +24 MB est.), 32% output churn between revisions three weeks apart. |
| Performance | Pass in isolation (~4.5× on the accepted files, similar harness RSS) — plausibly driven largely by OXIDE parsing each file five times (7.5 vs 1.5 parse-equivalents), which is addressable in-house (#24); not proven. |
| Determinism | Pass (both). |
| Independent quality advantage | **Not shown.** CodeGraph-only output is policy-shaped (fields, consts, anonymous classes, docstrings); no evidence it improves retrieval. |

The negative result is preserved in `results/`. The current extractor
stays.

## 13. Smallest justified next action

No CodeGraph follow-up. The one measured, in-house opportunity is #24
(one-parse extraction): this harness's `bench` mode already reports
OXIDE's seam at 7.5 bare-parse equivalents per file, which is #24's
baseline. Separately, the Go function-local `var` capture (§6) is a
concrete input for #26, together with the grouped-declaration nesting.
Neither is started here.
