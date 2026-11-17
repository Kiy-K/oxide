# PHP v1 language slice — design (OXIDE v0.2.0)

## Intent

Add PHP as a first-class language slice so PHP repositories get the same
deterministic indexing, graph, retrieval and navigation as the existing
languages. The slice must be conservative: it reports what the source and
`composer.json` actually prove, keeps ambiguous and unresolved references as
facts, and never invents edges for dynamic calls or constructs it cannot parse.

## Goals

- Declarations: functions, methods, classes, interfaces, traits, enums,
  constants, enum cases, properties, namespaces.
- Imports and aliases: `use` (class/function/const, including group `use`),
  `use ... as`, fully-qualified names, `namespace` (bracketed and unbracketed).
- Inheritance and interfaces: `extends`, `implements`, interface `extends`,
  trait `use` inside a class body.
- Conservative cross-file references resolved from namespace/import context
  plus Composer autoload evidence (PSR-4, PSR-0, classmap, files).
- Four-way confidence: `proven`, `heuristic`, `ambiguous`, `unresolved`.
- Mixed PHP/HTML files and PHP-only files both handled correctly.
- No new MCP tools; navigation flows through existing search/context/outline/
  inspect/refs/blast-radius.
- Deterministic regression suite; measurements for correctness, index speed
  and navigation coverage; framework validation; packaging/wheel check.

## Non-goals / anti-goals

- No dynamic-call edges (variable calls, callables, `call_user_func`, magic
  `__call`, reflection, service containers, string class names).
- No emulating PHP's runtime autoloader, no executing PHP or Composer.
- No embeddings, judges, or model calls (SPEC vectorless rule).
- No changes to other languages' behavior or frozen evaluation baselines.
- No revival of the v1 conformance format as a v2 gate.

## Constraints / research verdicts

- **Grammar/packaging:** `tree-sitter-php==0.25.1` is the current wheel;
  requires Python ≥3.10, `tree-sitter~=0.25` (compatible with the pinned
  `>=0.25,<0.27`). Wheels ship for macOS x86-64/arm64, manylinux and musllinux
  x86-64/arm64, win amd64/arm64 — covers the CI matrix. The package exposes a
  full PHP grammar (PHP + inline HTML/text) and a PHP-only grammar; the full
  grammar is required so mixed files parse without pre-extraction. Pin the
  exact version in `pyproject.toml` + `uv.lock` as the other grammars are.
- **Composer semantics:** PSR-4 maps a namespace prefix (trailing `\`
  significant) to a path, and the prefix is *not* repeated in the path; PSR-0
  is the older convention where it is; classmap scans `.php`/`.inc`; `files`
  are unconditional includes. PSR-4 is the reliable evidence; `classmap` is
  heuristic; `files` gives file-level, not symbol-level, evidence.
- **Historical golden:** `fixtures/conformance/php.golden.json` is frozen v1
  output (module entities, `content_hash`) from `25e51b6`/`32ff85e`. No current
  test reads it. Use it as a construct checklist only; do not regenerate it.
- **CAP-003 / CORE-001:** language support must extend the declarative extraction
  path; any procedural walker needs a stated gap analysis. Queries collect every
  fact, but only a resolver can follow namespace aliases, `use` bindings and
  Composer prefix mappings across files — a tree-sitter query has no cross-file
  scope, so that is the concrete gap. Composer evidence reaches core as **captured
  bytes**: `derive` selects captured `composer.json` files from its read view and
  passes them to `php_composer`, which parses JSON in memory. Core never opens a
  file. Follow the C# slice: a query-driven syntax/facts module, a model, a
  resolver, and a thin parse entry point, integrated in `core/indexing.py`.
  Source files stay at or below 500 LOC.
- **PHP-specific semantics:** names (classes, functions, namespaces, methods) are
  case-insensitive; resolution folds case while entity keys keep source spelling.
  Unqualified class names resolve in the current namespace but do **not** fall back
  to global; functions and constants do (heuristically). `.inc` is not indexed in
  v1 — it is not reliably PHP in a mixed repository and would violate CAP-001's
  explicit-scope rule.
- **Derivation identity:** adding PHP to `FACTS_FORMAT` and `components()` changes
  the derivation id. Per `fixtures/eval/README.md` and the C# precedent, relabel
  the frozen ContextBench manifest in place with a README note after confirming a
  fresh run yields byte-identical metrics; do not regenerate results or metrics.

## Approaches

1. **Full slice mirroring C# (chosen).** New `php_syntax.py`, `php_facts.py`,
   `php_model.py`, `php_resolve.py`, `php_composer.py`, `php.py` under
   `src/oxide/core/languages/`; register `.php` (and `.phtml`) in the dialect
   registry; add resolution to `derive`; add `tests/python/test_php.py` in the
   v2 conformance style. Trades more code for meeting every requirement,
   especially Composer evidence and confidence levels.
2. **Declarative-only extraction (rejected).** Queries alone could emit
   declarations and syntactic references, but cannot resolve namespace
   aliases or Composer mappings, so it fails the proven/heuristic split and
   the cross-file navigation requirement.
3. **Revive the v1 golden as the gate (rejected).** Its format is not what the
   v2 store publishes; it would test the wrong contract.

## How it will be built

- **Dialect + capture:** `.php`, `.phtml`, `.inc` map to `php`; parse with the
  full grammar so inline HTML is inert `text`/`php_tag` nodes. UTF-8 and
  depth guards match the other slices; parse failures produce diagnostics and
  explicit `partial`/`unsupported` coverage (CAP-002).
- **Syntax/facts:** query-driven extraction of namespace declarations,
  `use` statements (single, group, function/const, alias), class-like
  declarations with `extends`/`implements`/trait uses, members, functions,
  constants and enum cases. Record source spans for every declaration and
  reference site; record lexical scope for name lookup.
- **Resolution:** build a per-file symbol table (namespace + alias map),
  then a repo-wide index of fully-qualified declarations. Resolve a reference
  in this order: (1) fully-qualified name → exact symbol = **proven**;
  (2) import alias → target FQN found = **proven**; (3) current namespace +
  name found = **proven** when unique, **ambiguous** when multiple; (4) Composer
  PSR-4/PSR-0 prefix → file exists → **proven**, else **unresolved**;
  (5) classmap entry → **heuristic**; (6) fallback global name = **heuristic**
  when a unique definition exists, else **unresolved**. Trait uses and
  `extends`/`implements` use the same pipeline. Each relation records its
  evidence kind and source span. Unsupported constructs produce no edge.
- **Identity/edges:** follow the C# slice's `Entity`, `Edge`, `Relation`,
  `Use` and key conventions so navigation, outlines and blast radius work
  without new MCP surfaces. Unknown endpoints and ambiguous candidates are
  retained, never silently dropped (CAP-004/CAP-005).
- **Navigation:** no adapter work expected if the facts are emitted in the
  common shape; add tests that drive `search`, `context`, `outline`,
  `inspect`, `refs` and blast radius through PHP files.
- **Tests:** `tests/python/test_php.py` (v2 conformance style) plus PHP cases
  added to the navigation regressions. Deterministic, private data locations.
- **Validation:** check in representative Laravel-, Symfony- and WordPress-
  shaped fixtures (PSR-4, PSR-0/classmap, global-function style respectively)
  as deterministic regression inputs. Any live upstream clone stays
  uncommitted and is used only for a local measurement note.
- **Oracle/measurement:** use `php -l` where PHP is available as a parse
  oracle (syntax acceptance only, not a semantic oracle); report index time
  vs. corpus size, declaration/reference counts, and resolved-vs-unresolved
  ratios on the fixtures. No quality claim without numbers.
- **Branch/verification:** implement on `feat/php` (based on `main`); run
  `mise run verify`, `mise run tier`, and an independent zero-context review;
  commit; report measured findings and limitations before acceptance.

## Decisions settled during implementation

- Grammar node names were confirmed against the installed 0.25.1 wheel; the
  binding entry point is `language_php()` (full grammar), not `language()`.
- `.inc` is not indexed in v1 (see constraints).
- Adding PHP is not a quality/performance claim, so no ContextBench quality gate
  applies; measurements are observed numbers, not gates. The derivation id is
  relabelled, with byte-identical metrics, per the documented pin-relabel process.
- The validation corpus is one deterministic ~11-file fixture shaped with PSR-4,
  PSR-0/classmap and global-function conventions; no live upstream clone is
  committed. `php -l` is used only as a syntax-acceptance oracle when available.
