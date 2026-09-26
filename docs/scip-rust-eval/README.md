# SCIP-assisted structural evidence for Rust (rust-analyzer)

**Verdict: reject — stopped at the pre-registered gate.** On ContextBench's
20 verified Rust instances, making OXIDE's structural edges *exact* with
rust-analyzer's own resolution — per seed, iterated until every structural
slot holds a SCIP-confirmed item — admitted gold into the final bounded
context in **0 of 20** instances (stop line: ≥ 2). Structural evidence
contributes **0** gold in any of the 20 shipped packs; **74%** of missed
gold has no name-tier link to any direct seed (a seed problem SCIP cannot
touch); where rust-analyzer confirms a real seed→gold edge (8 instances),
the fixed expansion slots end up held by SCIP-confirmed *but irrelevant*
targets or by Markdown-seed items SCIP cannot judge. A
single-file edit costs a full rust-analyzer re-run: **8.8 s / 1.63 GB vs
OXIDE's 0.23 s / 98 MB** for a body edit that re-embeds (38x wall, 17x
RSS) on clap; cold generation across the 20 commits takes 3.7–169 s and
up to 4.45 GB in one process (nushell). No
production code, schema, id, default or roadmap entry was changed.
Results in §4–§7; the research that framed them in §1–§2.

The measurements surfaced one confirmed retrieval gap that is *not*
structural and is larger than anything SCIP could fix: OXIDE's scanner
denylist drops every directory named `build` (and `out`, `dist`, …) at any
depth, even when git-tracked — clap's entire core `src/build/` is never
indexed (§6.1).

Predecessor: `docs/scip-provider-eval/` rejected SCIP (scip-python,
scip-typescript) *as an extraction provider* on incremental cost (gate 4:
90–210x OXIDE's single-file update) and left one shape open — an opt-in,
out-of-band relation overlay, never on the `oxide watch` path. This
investigation asks the narrower follow-up for Rust: does rust-analyzer's
SCIP export add structural evidence that OXIDE's bounded context would
actually use, at a cost that overlay shape could carry?

## 1. OXIDE's structural evidence today (what SCIP could replace)

All of it is bare-name tier; nothing resolves *which* same-named definition
a use means.

| Consumer | Relation | Source | Tier |
| --- | --- | --- | --- |
| `RelationGraph::neighbors` (search expansion, review, git enrichment) | `parent`/`child`/`sibling` | parser containment, looked up by qualified-name *string* repo-wide (`by_qualified` takes the last in corpus order) | resolved within a file; **name-matched across files** — Rust qualified names carry no module path, so `parent←Command.render_usage_` can land on any of 9 `Command`s |
| | `imported-definition` | `use` path → one indexed file (`resolve_module_with`), narrowed to names the seed references | resolved file, name-matched symbol |
| | `uses` | `extract_references` token ∩ known definition names, narrowed to imported files when any candidate is import-backed | heuristic |
| | `test` | test symbols whose `references` contain the seed's bare name | heuristic |
| `evidence/coordinator.rs::structural_evidence` | callers (≤2 per seed, seed-file scope) | `symbol_relations.calls` (AST call expression, bare callee name) | heuristic target |
| `blast_radius.rs` (bounded, repo-wide) | callers, implementors, 1 transitive hop | `calls`, `bases` | heuristic target |
| `git_graph_enrichment` | `uses`/`test` neighbors + scoped callers of changed symbols | same | heuristic |

Rust specifics (`rust_callers.scm`, `rust_implementors.scm`, conformance
golden): `calls` = last path segment of call/method/macro callee;
`bases` = trait name from `impl Trait for Type` and supertrait bounds,
attributed to the same-named struct via LANG-002's ladder step (c);
`qualified_name` carries no module path (`Backend.get`), so two
`fn new` in different modules share a name. Aliased imports
(`use x as y`) are a documented gap (`relations.rs`
`an_aliased_import_is_a_known_gap_shared_with_uses_not_new_here`).

**Measured failures vs hypothetical ones.** The only measured structural
failure is `docs/ranking-fusion-eval/` §7.1: `uses` neighbors at **0.4%
precision** (16 per seed) and top-3 seeds gold only **17%** — structural
evidence is informative only when the seed is right. That audit is
Python (pylint, pytest, requests, flask) and TypeScript (zod) only; **no
Rust structural failure has been measured.** SCIP can only fix the part of
the 0.4% that is *wrong-definition* fan-out, not correct-but-irrelevant
neighbors, and cannot fix wrong seeds. Everything else in this section is a
hypothetical improvement until the gap screen (§3) says otherwise.

## 2. SCIP and rust-analyzer — what the sources say

Read from source, not prose: `rust-lang/rust-analyzer` master
`crates/rust-analyzer/src/cli/scip.rs`, `crates/ide/src/static_index.rs`,
`crates/rust-analyzer/src/cli/flags.rs`; `scip-code/scip` `scip.proto`;
crates.io (2026-09-26).

### Generation is a full-workspace, one-shot analysis

- `rust-analyzer scip <path>` accepts only `--output`, `--config-path`,
  `--exclude-vendored-libraries`, `--num-threads`. **No per-file target, no
  incremental mode.** The CLI's own help: subcommands "do not provide any
  stability guarantees and may be removed or changed without notice".
- It calls `load_workspace_at` with `load_out_dirs_from_check: true`
  (runs `cargo check` for build scripts / `OUT_DIR`), a sysroot
  proc-macro server, and `prefill_caches: true` — i.e. it resolves the
  whole dependency graph and sysroot, then `StaticIndex::compute` walks
  every workspace module and, per unique definition, computes hover,
  documentation, moniker, signature and kind. **Build scripts and the
  proc-macro server cannot be turned off for SCIP export**: `scip.rs`
  builds its `LoadCargoConfig` with those two values hard-coded, so a
  `--config-path` with `cargo.buildScripts.enable = false` /
  `procMacro.enable = false` has no effect — measured: byte-identical
  `.scip`, the same build scripts in the log (§5). (Config errors are only
  logged at `error!`, so "accepted" is not observable either way.)
- rust-analyzer *is* incremental — as a long-lived LSP server over its
  salsa database. The SCIP export does not reuse any of that across runs:
  each invocation rebuilds from nothing. Keeping the incremental engine
  means running a language server or embedding `ra_ap_*` (0.0.352, MSRV
  1.98, weekly releases, no API stability) — both excluded by the task
  and a maintenance cost OXIDE does not carry today.

### What the index contains

| SCIP field | rust-analyzer | scip-python/-typescript (prior eval) |
| --- | --- | --- |
| `SymbolInformation.kind` | **populated** (Struct, Trait, Method, TraitMethod, StaticMethod, Function, …) | empty |
| `display_name`, `signature_documentation`, `documentation` | populated | empty |
| `enclosing_symbol` | locals only | empty |
| `relationships` (`is_implementation`, …) | **always empty** (`relationships: Vec::new()`) | populated |
| occurrence `symbol_roles` | `Definition` only — no Import/Read/Write | Definition, no Import |
| occurrence `syntax_kind` | unset | — |
| occurrence `enclosing_range` | the referenced definition's body, same file only | — |

Consequences:

- **Implementors are not emitted.** A trait impl is visible only in method
  descriptors — `module/impl#[MyStruct][MyTrait]func().` — where
  `MyTrait` is the trait's *name* (display text, bare-name tier, same as
  OXIDE's `bases`). An exact implementor edge must be derived from the
  resolved reference occurrence of the trait path inside each `impl`
  header's range.
- **Calls are not distinguished from other references.** Any non-definition
  occurrence of a callable symbol is a "reference"; SCIP says *what* a name
  resolves to, not *that* it was called. Comparisons with OXIDE's AST
  `calls` must say so.
- **Caller attribution is ours to do**: occurrences carry a range, not the
  enclosing definition; mapping them to OXIDE symbols needs
  position-granular span containment.
- **Macro-expanded references are resolved** (`descend_into_macros_exact`),
  something Tree-sitter cannot see at all.

### Symbol identity

`rust-analyzer cargo <crate> <version> <descriptors>`; version comes from
`Cargo.toml` (`.` when absent), not git — a version bump rewrites every
symbol string (same landmine as the prior eval, milder trigger). Known
duplicate-symbol bugs, printed to stderr: inherent impls
(`impl#[SelfType]`, only the first gets `SymbolInformation`, #18772),
items nested in functions (#18771), example binaries shadowing the library.
A raw SCIP symbol can never be an OXIDE id (LANG-004); a join has to go
through file + span.

### Consumption

`scip` crate 0.10.0 (Apache-2.0, MSRV 1.81, repo `scip-code/scip`) — the
same crate rust-analyzer itself writes with; protobuf-based. Consumption
cost is measured in a scratch crate outside this repo so `Cargo.toml`
stays untouched.

## 3. Protocol (as run)

Pre-registered before any screen ran: *stop if an oracle that makes every
edge exact adds gold to the final bounded context of fewer than 2 of the
20 instances.* Everything below runs the real `target/release/oxide`
(`d1333a9`) on benchmark copies (`cp -a` + `VACUUM INTO`, `meta.root`
repointed); production code, schema and ids are untouched.

1. **Gap screen** (`scripts/gap_screen.py`): shipped default embedder,
   `oxide context --json` at 4096 tokens (balanced), plus `--blast-radius`;
   gold spans mapped to innermost OXIDE symbols; each missed gold symbol
   classified against the pack's direct seeds as *unlinked* /
   *linked-unambiguous* / *linked-ambiguous* (any name-tier relation,
   either direction; ambiguous = the linking name has > 1 definition).
2. **Oracle ladder.** How the decision measure evolved, in order: v1 was
   declared the stop measure first; the independent review showed it was
   too narrow (containment untouched, competing noise left in the slots);
   a gold-knowing fixpoint then crossed the line (5/20); SCIP-exact became
   the decision measure because it is what the pre-registered "every edge
   exact" wording means; the advisor review then found SCIP-exact's
   one-shot form leaked against SCIP (a global keep set, containment
   untouched), so the final decision measure is its per-seed fixpoint.
   Every bound is validated by an A/A run
   (byte-identical pack, `index_generation` unchanged, persisted BM25 in
   use) and positive controls (`raw/admission_oracle_positive_control.json`:
   renaming a `uses←` item removes it; deleting a caller's `calls` row
   removes an `ast-grep-caller←` item):
   - **v1, ambiguous names** (`admission_oracle.py`): rename every non-gold
     definition of each ambiguous linking name, delete non-gold `calls`
     rows targeting it. 9 eligible instances.
   - **Gold-knowing fixpoint** (`admission_oracle.py --full --rounds 40`,
     added after independent review): additionally make cross-file
     containment exact, then repeatedly evict *every* non-gold structural
     item until gold appears or none is left. An upper bound for any
     structural selection policy — it also evicts correct edges, so it is
     *not* a bound attributable to SCIP.
   - **SCIP-exact, one shot** (`scip_exact_oracle.py`): using
     rust-analyzer's own resolution, rename every definition that no seed's
     SCIP references resolve to, and delete `calls` rows from symbols with
     no SCIP reference to the seed. Seeds = direct pack items *and*
     `omitted` candidates (search-expansion seeds can be subsumed out of
     the pack). No gold protection. Generous to SCIP: the rename is global,
     so it also prunes Markdown seeds' fan-out, which an overlay could not.
   - **SCIP-exact per-seed fixpoint** (`scip_exact_oracle.py --fixpoint 40`)
     — the decision measure: every structural pack item is judged against
     the seed in its own reason tag (`uses`/`imported-definition` must be
     in that seed's resolved references; callers and `test` must reference
     the seed; containment must stay in the seed's file) and evicted
     unless SCIP confirms it, iterated until the slots hold only
     SCIP-confirmed items. *Realistic* mode keeps items from seeds without
     a SCIP document (Markdown), as an overlay must; *generous* mode
     (`--evict-nonscip`) evicts those too.
3. **Cost** (`cost_screen.sh`, `measure.py` — GNU time is absent: wall,
   largest-process RSS, summed process-tree RSS sampled at 50 ms): clap @
   `6eacd8a7` in detail, rust-analyzer generation on all 19 other commits.
4. **Edge agreement** (`edge_agreement.py`): OXIDE `calls`/`bases` vs
   rust-analyzer references on clap @ `6eacd8a7`; rust-analyzer is the
   reference, not ground truth.
5. Hand-labeled precision/recall fixture — **not run**: conditional on
   oracle headroom, which the decision measure did not show.

Pinned: rust-analyzer `2026-09-21` standalone (`0.3.3057-standalone`,
sha256 `10d555c6…`), rustc 1.98.0 + `rust-src`, `scip` 0.10.0; manifest in
`raw/manifest.json`.

## 4. Bounded-context result (the decision)

Unit is the instance: whole-file gold spans (clap `help.rs` 1–495,
nushell's 48 symbols) make pooled symbol counts misleading. Coverage counts
non-module pack items only — a module item's span covers its whole file
but its snippet is capped (`7acaca55` reads 23/23 "covered" by span, 0 by
non-module items).

| 20 verified Rust instances, default `context` | |
| --- | --- |
| instances with ≥ 1 gold symbol in the pack (non-module) | 7 |
| instances where any gold arrived via structural evidence | **0** (also 0 with `--blast-radius`) |
| structural pack items (default / blast) | 36 / 49 — **all non-gold**; 17 / 20 linked only via an ambiguous name |
| instances whose missed gold includes *unlinked* symbols | 16 (164 of 222 missed symbols, 74%) |
| instances with *linked-unambiguous* / *linked-ambiguous* missed gold | 9 (38 symbols) / 9 (20 symbols) |
| pack `used_tokens` vs 4096 | median 1,620, max 2,630 — the budget never binds |
| gold in `omitted`, baseline | only `beyond primary cap` / `per-file diversity cap`; never over budget |

| oracle | instances run | instances gaining non-module gold |
| --- | --- | --- |
| v1 ambiguous names | 9 | **0** — no pack changed at all |
| gold-knowing fixpoint | 20 | 5 (`16860730`, `3c69099b`, `627665a2`, `92158fba`, `aa16b8b2`); 11 still had non-gold structural candidates after 40 rounds |
| SCIP-exact, one shot | 20 | 0 |
| **SCIP-exact per-seed fixpoint, realistic (decision)** | **20** | **0** — final slot holders: 16 SCIP-confirmed non-gold, 10 from Markdown seeds, 2 `test←` items eviction cannot remove |
| SCIP-exact per-seed fixpoint, generous | 20 | 1 (`16860730`) — via `uses←crates/nu-parser/README.md`, a Markdown seed, and only because the harness never evicts gold: not SCIP-attributable |

Why the fixpoint's 5 do not transfer to exact resolution: in two
(`16860730`, `3c69099b`) gold arrived by `uses←` from a *Markdown* seed
(README/CONTRIBUTING prose) — no SCIP document exists for it; in one
(`627665a2`) it arrived by a bare-name collision between different
example binaries' `main` functions, an edge exact resolution *removes*;
in the other two, rust-analyzer confirms real seed→gold edges, and the
per-seed fixpoint still admits nothing — in `aa16b8b2` the two expansion
slots converge to SCIP-confirmed `InstrumentArgs` and `AsyncInfo`; in
`92158fba` the exact graph yields no structural candidate at all (the five
primary slots go to direct hits; gold `AppSettings` sits in `omitted` as
`beyond primary cap`), so its fixpoint admission came from edits beyond
exact resolution. The general reason holds across all 20: rust-analyzer
confirms real seed→gold edges for missed gold in **8 instances** (missed
gold only, excluding gold that merely lies inside a module seed's own
span), yet exact resolution admits none, because the expansion
is capped (`CONTEXT_EXPANSION_TOTAL = 2` neighbor items per request,
fixed neighbor order parent → siblings → children → uses → imports →
tests, callers from the top-2 seeds only, `CONTEXT_MAX_ITEMS_PER_FILE =
2`) and those slots are filled by *correct but irrelevant* targets before
gold. Exactness of a link does not decide admission **under the shipped,
frozen caps**; changing the caps is a different experiment (and the
ranking-fusion audit already found structural evidence informative only
when the seed is right).

The alias/macro-expansion pathway the name-tier superset argument does not
cover was checked with SCIP on instance `0e4346f7` (`scip_exact_oracle.py
--link-check`, seeds including omitted candidates): no SCIP edge from any
seed to any gold symbol (`raw/scip_link_check_0e4346f7.jsonl`).

## 5. Cost

clap @ `6eacd8a7` (252 `.rs` files, 55.7k lines):

| | wall | CPU (user) | largest process | process tree peak | artifact |
| --- | --- | --- | --- | --- | --- |
| `cargo fetch` (prerequisite) | 3.6 s | 1.2 s | 206 MB | 208 MB | lockfile resolved today — clap had none |
| rust-analyzer `scip`, cold target, run 1 / 2 | 13.50 / 13.48 s | 35.4 / 35.5 s | 1.58 GB | 2.04 / 1.94 GB | 6.45 MB `.scip` |
| same, build scripts + proc macros "disabled" | 13.34 s | 35.5 s | 1.62 GB | 1.90 GB | **byte-identical** (config ignored, §2) |
| same, warm cargo target | 13.30 s | 33.5 s | 1.58 GB | 1.68 GB | identical |
| same, after a comment-only edit | 9.81 s | 15.1 s | 1.57 GB | 1.68 GB | new file |
| **same, after a function-body edit** | **8.75 s** | 13.5 s | **1.63 GB** | 1.75 GB | new file |
| `scip` crate decode (+ flatten to JSON) | 48 ms (96 ms) | — | 61 MB (81 MB) | — | 211 docs, 65,407 occurrences (16,379 local), 3,708 symbols; 21 ms decode in-process |
| OXIDE `index`, native default, cold | 7.1–7.9 s | 52–57 s | 0.40–0.42 GB | — | 10.1 MB `index.db` (embeddings + BM25) |
| OXIDE, native, after the comment-only edit | 0.21 s | 0.18 s | 83 MB | — | no re-embed (model never loads) |
| **OXIDE, native, after the function-body edit** | **0.23 s** | 0.24 s | **98 MB** | — | 1 symbol re-embedded, 2,474 reused |
| OXIDE `index`, hashed, cold / after comment edit | 1.19 / 0.14 s | 1.5 / 0.12 s | 42 / 30 MB | — | 11.9 MB |

- Single-file update: **38x wall, 17x RSS** for a body edit (47x / 19x for
  a comment edit, OXIDE's best case); rust-analyzer's cost is a full
  re-run for any edit. Same shape as the prior Python/TS gate-4 failure
  (90–210x).
- rust-analyzer generation over all 19 other commits
  (`raw/cost/ra-other-commits.jsonl`, cold cargo target): wall **3.7–169 s
  (median 21.8 s)**, largest process **0.62–4.45 GB (median 1.61 GB)**;
  nushell peaks at 4.6 GB for the process tree; three clap commits spend
  ~150–170 s mostly compiling build scripts and proc macros, which SCIP
  export cannot skip.
- Determinism: the two cold runs, the warm run and the "disabled" run are
  byte-identical (`raw/cost/*.sha256`).
- 568 duplicate-symbol reports covering **122 distinct symbols** on clap
  (rust-analyzer's own warning: lookups for them are ambiguous).
- Operational: every indexed repository needs a working toolchain and
  registry access (network on first run), and each cold run writes a cargo
  `target/` of hundreds of MB — the 20-instance run here filled a 7.7 GB
  tmpfs before those were cleaned between runs. OXIDE needs none of it.
- Consumption is cheap (20-crate scratch build, 880 KB binary, 21 ms
  decode); generation is the whole cost.

## 6. Correctness: OXIDE vs rust-analyzer edges (clap @ `6eacd8a7`)

`raw/edge_agreement_clap.json`. Callable "references" are every
non-definition occurrence of a function/method/macro — a superset of calls.

| `calls` | |
| --- | --- |
| OXIDE call edges / with a callee name defined in-project | 10,819 / 4,077 |
| … whose rust-analyzer target lies in a file OXIDE never indexed (§6.1) | **2,190** — the bare name bound to some *other* same-named symbol |
| … of the remaining 1,887: confirmed (≥ 1 candidate is the resolved target) | 1,185 (63%) |
| mean same-name candidates per in-project edge | 4.61 |
| wrong extra candidates carried by confirmed edges | 1,558 |
| rust-analyzer callable edges between indexed symbols / absent from OXIDE `calls` | 2,341 / 1,133 |

Most of the 1,133 are references that are not call expressions, but a real
recall gap is visible among them: **calls inside macro invocations**
(`assert_eq!(…, Opt::parse_from(…))`, `format!(…, escape_string(…))`) are
token trees to Tree-sitter, so OXIDE records no call; rust-analyzer
descends into the expansion.

| `bases` (impls defined in OXIDE-indexed files) | |
| --- | --- |
| OXIDE pairs / rust-analyzer `impl#[T][Trait]` pairs / both | 72 / 65 / 51 |
| OXIDE only (21) | supertrait bounds (`trait Parser: FromArgMatches + IntoApp + Sized`) and empty-bodied impls (`impl ExactSizeIterator for …{}`) — correct, but rust-analyzer emits no member symbol to carry them |
| rust-analyzer only (14) | impls OXIDE does not attribute (`impl Display for Shell`, `impl Parse for ClapAttr`, …) |

rust-analyzer's trait in the descriptor is display text, so SCIP adds
*coverage* here, not resolution.

### 6.1 Side finding: git-tracked `build/` directories are never indexed

`scanner.rs`'s `DENYLIST_DIRS` prunes any directory whose *name* is
`build`, `out`, `dist`, `coverage`, `vendor`, … at any depth, whether or
not git tracks it. clap's `src/build/` (App/Arg builders, 15 tracked
files, ~450 KB) is invisible to OXIDE — also why 2,190 of its call edges
point at the wrong definition. ContextBench gold files under such a
directory: **2 of 20 verified Rust instances** (`3be02163`, `627665a2`,
3 gold files each), 10/63 in `full` Rust, 20 instances across all
languages in `full` (Go 4, C 2, TypeScript 2, JavaScript 1, Python 1).
A confirmed retrieval failure independent of SCIP; the gap screen
undercounts gold for those two instances because that gold has no OXIDE
symbol. Not fixed here (production behavior).

### 6.2 Side finding: container-absolute gold paths

Some ContextBench rows store gold as `/workspace/<owner>__<repo>__0.1/…`
(verified: TypeScript 14, Go 9, C 8, Java 7, JavaScript 7, Rust 6, C++ 5).
`scripts/agent_eval/contextbench_run.py::evaluate_task` does not strip it,
so line/span/symbol metrics read 0 for those rows while file coverage
(normalized inside `Gold.files()`) does not. **1 of the 21** instances in
both the Tier A pin and `docs/ranking-fusion-eval/`'s ContextBench pin is
affected. This harness normalizes it (`gap_screen.normalize_gold`).

## 7. What SCIP resolves, what it does not

| | rust-analyzer SCIP | effect on OXIDE's bounded context (measured) |
| --- | --- | --- |
| which same-named definition a call/use means | yes (1,558 wrong candidates on clap's confirmed call edges alone) | none: the per-seed SCIP fixpoint admits gold in 0/20; real seed→gold edges exist for missed gold in 8 and lose to SCIP-confirmed irrelevant targets, Markdown-seed items, or the primary cap |
| calls inside macro invocations | yes | not reached from the seeds OXIDE picks (`0e4346f7` link check) |
| trait implementations | coverage only (descriptor text; `relationships` empty) | — |
| wrong seeds (74% of missed gold unlinked; Markdown/example modules as seeds) | no | — |
| primary / diversity / expansion caps deciding admission | no | — |
| files OXIDE never indexes (`build/`) | no — an overlay joins onto OXIDE symbols that do not exist | — |
| incremental update | no — full workspace analysis every time | 38x / 17x OXIDE's body-edit update; 3.7–169 s and up to 4.45 GB cold |

## 8. Integration options

1. **Keep OXIDE's structural engine unchanged — recommended.** The
   measured losses are seeds and the frozen pack/expansion caps, not edge
   resolution.
2. **Optional imported SCIP overlay** (`oxide index --scip`, the shape the
   prior eval left open) — rejected on evidence, not only cost: its
   measured bounded-context gain on this corpus is zero. It would also add
   a second authority beside SQLite whose staleness `index_generation`
   cannot see (the `.scip` is produced outside OXIDE's write paths), a
   toolchain + registry + build-script dependency per indexed repo, a
   symbol join through file + span with duplicate symbols to treat as
   ambiguous, and per-language indexers with different field coverage
   (Python/TS leave `kind` empty; Rust leaves `relationships` empty).
3. **Narrow SCIP-compatible techniques without the infrastructure** — none
   justified by this data. The two concrete defects SCIP exposed are
   fixable in OXIDE's own terms and are not SCIP techniques: the `build/`
   denylist (§6.1) and calls inside Rust macro token trees (§6). Neither
   is measured for bounded-context value yet.

## 9. Limitations

- Detailed cost on one commit (clap, 2021, lockfile resolved today);
  generation-only cost on 19 more. Single runs except the duplicated cold
  run (13.50 vs 13.48 s); the ratios are far outside noise.
- 20 instances, 13 from one repository; ContextBench gold is span-level
  and sometimes whole-file.
- The SCIP oracles remove wrong edges only; they do not add edges
  the name tier lacks (aliases, macro-expanded calls). Those were checked
  on one instance. They use line-granular attribution; the per-seed
  fixpoint never evicts gold (favorable to SCIP) and cannot evict `test←`
  items that come from module symbols' textual references (2 residual
  wrong slot holders).
- The fixpoint is truncated at 40 rounds (11 instances unconverged); it
  only selected where to look and is not the decision measure.
- Edge agreement uses rust-analyzer as reference and treats every callable
  reference as call-like.
- Harness defects found and fixed during the run are kept, with their
  discarded outputs, under `raw/discarded/`: unnormalized gold paths (1);
  a single-pass eviction mistaken for the upper bound (2); a
  qualified-name rename that breaks OXIDE's id recomputation on
  completion (3, 5 — modules first, then `ae8b362a`, rerun with qualified
  names preserved); SCIP-exact seeds limited to pack items (4); a
  `--evict-nonscip` predicate that silently kept Markdown-seed items (6).

## 10. Next action

Record this rejection; no SCIP work follows. The single smallest
justified next action from this investigation is a focused issue for
§6.1: measure, on the 20 affected ContextBench instances, whether
exempting git-tracked files from the `build`/`out`/`dist` directory
denylist changes bounded-context recall, before any scanner change.

**Follow-up done (2026-09-26):** that measurement is
`docs/scanner-tracked-dirs-eval/` — Git-tracked overrides for generic
skipped directories were **rejected** (one-repository benefit, control
regressions, line/span degradation, storage and literal-search cost,
watcher complexity). The `/workspace/` gold-path finding of §6.2 was fixed
in the scorer (`docs/contextbench-scorer-fix/`). The status of this SCIP
investigation is unchanged: **rejected**, kept as a negative result.

## Reproducing

```bash
S=docs/scip-rust-eval/scripts; PY=eval-agent/.venv/bin/python
$PY $S/gap_screen.py --out docs/scip-rust-eval/raw/gap_screen.jsonl
(cd $S && ../../../$PY admission_oracle.py --gap ../raw/gap_screen.jsonl \
    --out ../raw/admission_oracle.jsonl --work <scratch>/oracle)
(cd $S && ../../../$PY admission_oracle.py --full --rounds 40 --gap ../raw/gap_screen.jsonl \
    --out ../raw/admission_oracle_full.jsonl --work <scratch>/oracle_full)

# SCIP: pinned rust-analyzer in each instance's copy (delete its cargo
# target/ afterwards), then flatten with the scratch reader
# (raw/scipread.rs + raw/scipread.Cargo.toml, scip = "=0.10.0")
(cd <copy> && RUSTUP_TOOLCHAIN=1.98.0 rust-analyzer scip . --output <tag>.scip)
scipread <tag>.scip > <tag>.json
(cd $S && ../../../$PY scip_exact_oracle.py <tag> <tag>.json <scratch>/exact ../raw/scip_exact_oracle.jsonl)
(cd $S && ../../../$PY scip_exact_oracle.py <tag> <tag>.json <scratch>/exact ../raw/scip_exact_fixpoint.jsonl \
    --fixpoint 40 [--evict-nonscip])   # ae8b362a: ORACLE_KEEP_QN=1

# cost + edge agreement on clap @ 6eacd8a7
$S/cost_screen.sh <clap worktree> <rust-analyzer> <scipread> <out>
(cd $S && ../../../$PY edge_agreement.py <clap worktree> <out>/ra-default-cold-1.json ../raw/edge_agreement_clap.json)
```
