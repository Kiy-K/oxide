# Prior evidence (online research, 2026-10-19, before the freeze)

What is already known about structural file outlines as agent context, and what it
implies for this study's design. Claims are the sources' own; numbers are quoted from
them, not re-measured.

## ast-grep `outline` (arm B's tool)

- First released in **ast-grep 0.44.0 (2026-06-22)**; the extraction rules changed in
  every minor release since (0.44.1 bounded file queue, custom-language rules; 0.45.0
  ambient TypeScript modules; 0.45.1–0.45.3 C# signatures, embedded languages, signatures
  anchored to names; 0.50.0 Java fixes). The pinned 0.51.0 is ~4 months into the
  command's life. Source: GitHub release notes, `ast-grep/ast-grep`.
- **Top-level items only, by design.** Issue #2957 (a nested declaration produces no
  element and its members are attributed to the enclosing one) was closed *wontfix*:
  "ast-grep only supports top-level items. Nested items are considered as impl details."
- Issue #2910: decorated TS/JS declarations reported the decorator's line as the
  signature (closed). Python's from-import modules were fixed in 0.44.0 (#2758).
- **Upstream's own agent benchmark** (removed from the repository in #2763; recovered
  from commit `8a0be7e`, `benchmarks/outline-benchmark.md`): 56 `claude -p` runs
  (Sonnet 4.6), 7 repositories x 2 arms x 4 repeats, outline use forced in one arm,
  correctness scored as rubric coverage *relative to the no-outline answer*. Both arms
  reached 100 % diagnostic score everywhere. Median cost: 35 % lower (VS Code), 55 %
  (Django), 40 % (OkHttp), 25 % (Excalidraw), 12 % (Tokio), 11 % *higher* (Gin, 99
  files), even (Alamofire). Its own reading: "outline helps when it replaces broad reads,
  but hurts when it is added on top of already-sufficient grep/read exploration."
  Its claim is cost at equal (ceiling) correctness, not better navigation decisions.

## Outlines and skeletons in localization and repair

- **Agentless** (Xia et al., arXiv 2407.01489, § 3.1.2 and Table 2): files are
  compressed to a *skeleton* (class/function headers, class fields and method
  signatures, comments) for related-element localization. Skeleton vs complete file:
  58.33 % vs 53.67 % of issues whose localization contains the ground truth, 698 vs 778
  lines, $0.02 vs $0.15. So a skeleton can beat whole files for choosing *where* to look.
- **"What Context Does a Coding Agent Actually Need to Act?"** (arXiv 2607.09691): with
  localization fixed by an oracle, rendering the non-edited remainder as UML
  skeletons/signatures resolved 23/70 SWE-bench Verified multi-file issues vs 25/70 for
  deleting it (McNemar p = 0.754) and 19/70 for whole files. Signature + docstring
  answered 6/45 behavioural probes vs 27/45 for source. ~9 % of per-instance outcomes
  flip between identical temperature-0 runs. So a structural digest is not source
  evidence, and small arm gaps need repetitions.
- **CODESTRUCT** (arXiv 2604.05407): structure-aware read/edit actions over named AST
  entities: +1.2–5.0 % Pass@1 and −12–38 % tokens on SWE-bench Verified for most models.
  Evidence that targeted reads of named units help agents.
- **ContextBench** (arXiv 2602.05892, the source of OXIDE's ContextBench tasks):
  sophisticated scaffolding yields only marginal gains in context retrieval; models
  favour recall over precision.
- Related localization systems: LocAgent (arXiv 2503.09089, graph + BM25, 92.7 %
  file-level accuracy on SWE-bench Lite with a fine-tuned model), OrcaLoca (arXiv
  2502.00350). Aider's repo map (aider.chat/docs/repomap.html) ranks files on a
  dependency graph and fits key definitions into `--map-tokens` (default 1k); no
  measured effectiveness is published there.

## Consequences for this design

1. Measure **navigation decisions** directly (Agentless's question), not cost at a
   ceiling (upstream's), and charge the overview against the same budget.
2. Keep **source evidence and structural overview apart** (2607.09691): an outline
   item in the payload must never count as gold source; A0 isolates freed budget.
3. **Repetitions and a noise criterion** (~9 % flips at temperature 0).
4. **Stratify by repository size**: upstream's gains were on large repositories, losses
   on ~100-file ones (here vscode/MUI vs the fixtures).
5. Treat B's **nesting, decorator and rule churn** as dependency risks to measure,
   with the version pinned.
