# #42 TreeSelect v0 — implementation decisions (fixed before any scored run)

The protocol (`PROTOCOL.md`) leaves these details open. Each was decided on the repo fixtures
(`fixtures/py_repo`, `fixtures/ts_repo`) before the harness touched any of the 149 tasks, and is
hashed in `results/prereg.sha256` together with `research.patch` and `scripts/`.

**Base.** Research binary = frozen commit `96abae8` + `research.patch` (detached worktree in
`~/.cache/oxide-treeselect-eval/wt`). Arm A's baseline binary is #40's cached
`target-frozen` (built at `84c4d4a`). `git diff 84c4d4a 96abae8 -- src Cargo.toml Cargo.lock build.rs examples`
is empty.

**Switch.** `context::treeselect_research::set(trace, arm)`. With no arm, the production code path runs
unchanged; tracing only records the delivered snippet spans (`render_snippet_span`, the same as ch5/#40).
An arm replaces only what follows the relevance floor (`treeselect_research::pack`). Arms with step-5
expansion also skip production's RelationGraph expansion loop.

| harness arm | protocol arm | `tree` | `collapse` | step-5 expansion | budget |
|---|---|---|---|---|---|
| A | A | — | — | — (production) | 4096 |
| B | B | no (production role-then-score order) | no | no (production) | A's `used_tokens` |
| TX | T=−x | yes | yes | no (production) | A's `used_tokens` |
| T | T= | yes | yes | yes | A's `used_tokens` |
| TC | T=−c | yes | no | yes | A's `used_tokens` |
| T4096 | T4096 | yes | yes | yes | 4096 |

**Interpretations.**

1. *Pool.* This is production's post-dedup, post-floor `kept`. Production dedup removes every nested
   pair: overlap is measured against the smaller symbol, so containment counts as 1.0 > 0.8. Pooled
   symbols therefore never contain one another.
2. *Tree symbol levels.* Because of (1), symbol containers come from the **index**. A leaf's container
   chain is every indexed symbol in its file, except `Module` and `Import`, whose span strictly contains
   the leaf's span, ordered outermost first (ties: lower id). A container with one pooled descendant is
   removed by flattening.
3. *Flattening.* When a node is pushed onto the frontier, it is replaced by its only child, repeatedly.
   The flattened node takes the child's identity, so collapse is checked on the deeper container.
   NodeScore is unchanged by flattening, because the leaf set is the same.
4. *NodeScore.* Computed in f64 from the unchanged f32 candidate scores. Ties use the node path (the
   directory for a directory node, otherwise the file), then the symbol id (0 for directory and file nodes).
5. *Collapse.* A file node's container is the file's indexed `Module` symbol (lowest id). A symbol
   node's container is itself, when its kind is `Class`. "Full body" means `estimate_tokens` of the
   container's whole line span. The collapsed item has the highest leaf score, the best leaf role
   (primary < dependency < test) and the union of the leaves' reasons, and its symbol is completed from
   the store.
6. *Step 5.* The anchors are the first 5 *offered* items in selection order (leaf or collapsed container),
   whether or not they fit the budget. Neighbors already in the pool, already offered or already admitted
   are skipped. Production's caps apply (2 per anchor, 2 in total, counted before dedup), with score
   anchor × 0.4 and role from `Role::assign`. Admitted neighbors are completed, then production's dedup
   rules (module subsumption; > 80 % overlap with an offered item or an earlier neighbor in the same file)
   and the relevance floor are applied. Survivors are offered after all tree items.
7. *Packing.* Production's fill loop with the 1-test cap, without the primary and per-file caps. An item
   that does not fit is skipped and the loop continues, as in production. Items are output in
   production's role-then-score order.
8. *Token matching.* For each task, the harness runs A first and sets `ContextOptions::budget_tokens` to
   A's `used_tokens` for B, TX, T and TC.
9. *Repeats.* Every arm runs twice in the same process. `repeat_identical` compares the full pack JSON
   and the trace.
10. *O1.* `search_ms` is one timed `engine.search` call (limit 50, hybrid, balanced), as in ch5.
    `context_ms` is the minimum of the two runs. Latency gate: median over tasks of (T= − A) ≤ 5 % of
    median `search_ms`. Memory gate (ch5's method): VmHWM on the 3 largest corpora by symbol count, in
    separate processes `--arms A` vs `--arms A,T` (an upper bound on T='s overhead), alternating order,
    2 reps, max per configuration; every corpus must be ≤ 5 %.
11. *R1.* The count part (regressions ≤ improvements, |Δ| > 1e-12) is computed. The "explained" part
    is a written per-task cause list in the README.
12. *Disposition mapping.* P1 fails → INCONCLUSIVE. Q1, Q2 or E1 fails → REJECT (TreeSelect v0). All
    pass → PROMISING. The protocol has no rule for Q1, Q2 and E1 passing while R1 or O1 fails; that case
    is reported as NOT PROMISING (not a REJECT of the ordering).

**Fixture checks (debugging only).**

- All arms deterministic over two runs.
- No duplicate packed keys, and `used ≤ budget`.
- `scripts/tree_check.py` (an independent Python re-derivation of steps 2–4 from B's offers) reproduces
  T=−x's offer order on 11/11 fixture queries.
- Research `oxide context --json` with the switch off is byte-identical to the frozen binary on 11/11
  fixture queries.
- Arm A's traced packs equal the frozen production packs.

## Amendment 1 (2026-10-04, after the frozen run; spec-conformance fixes only)

The frozen run (`results/run1-frozen/`, disposition INCONCLUSIVE, harness validity) showed two places
where the code did not implement `PROTOCOL.md`. Both fixes follow the protocol's own wording. No
threshold, constant, arm, metric or gate changed, and nothing was chosen by its effect on results.

1. *File container (protocol step 4: "a file `Module` symbol").* The code took the lowest-id
   `kind = module` symbol in the file. In these corpora that kind also covers inner modules and
   namespaces (Rust `mod tests`, `mod test;`, C++ `internal.digits`), so leaves were "collapsed" into
   inner modules that do not contain them (T=−x: 18 of 27 collapses, 13/149 tasks). The fix: a file
   node's container is only the file-level symbol `<file>:__module__`.
2. *Root flattening (protocol step 4: "the frontier starts with the root's children"; this file's
   §3: flattening applies to nodes pushed onto the frontier).* The code also flattened the root. When
   every pooled leaf sat in one file, the frontier then started at that file's children, so the file
   node was never opened or checked for collapse. The fix: the frontier is the root's children, each
   flattened.

`scripts/tree_check.py` had copied both readings and was corrected the same way. A scratch Rust
fixture (two functions plus `#[cfg(test)] mod tests` in `src/lib.rs`) now collapses into
`src/lib.rs:__module__`. The py/ts/rs fixture checks pass: tree check 12/12, switch-off parity 12/12,
deterministic repeats, no duplicate keys, `used ≤ budget`. The amended harness is hashed in
`results/prereg-amendment1.sha256` and committed before the rerun.
