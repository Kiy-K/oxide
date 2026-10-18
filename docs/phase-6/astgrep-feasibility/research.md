# ast-grep-py 0.51.0: background research

Desk research done before the preregistration, 2026-10-18. **Measured** means run
here (scratch venv `~/Projects/oxide-eval-data/oxide-astgrep-eval/venv312`, Linux
x86_64, CPython 3.12); **documented** means upstream docs or source at tag `0.51.0`;
**inferred** is labelled.

## What it is

- Python binding (PyO3 0.29) over `ast-grep-core`, `ast-grep-config`,
  `ast-grep-language` and `ast-grep-dynamic` (documented, `crates/pyo3/Cargo.toml`).
  MIT. Released 2026-10-15; 0.50.0 was 2026-10-09 and 0.45.x ran Jul–Aug 2026, so it
  moves fast and is pre-1.0.
- API: `SgRoot(src: str, lang).root()` → `SgNode` with `find`/`find_all` (a pattern,
  `kind`, `regex`, relational `inside`/`has`/`precedes`/`follows`, composite
  `all`/`any`/`not`, `nthChild`, `range`, or a `Config` with `constraints`/`utils`),
  `get_match`/`get_multiple_matches` for metavariables, `range()`, `kind()`,
  `text()`, navigation, and `replace`/`commit_edits` for rewriting (documented).
  One source string at a time: there is no file walking, ignore handling or
  parallelism in the Python API (documented; the CLI has those).

## Languages and grammars

- One 46,145,912-byte native module bundles every built-in grammar; the installed
  package is 45 MB (measured). The cp312 manylinux x86_64 wheel is 5.66 MB
  (PyPI). No per-language opt-in.
- Built-ins include Python, TypeScript, TSX, JavaScript, Java, Go, C, C++, Rust,
  Kotlin, Swift, C#, Ruby, PHP and more (14 checked, measured). Grammars resolved in
  the 0.51.0 `Cargo.lock`: tree-sitter core 0.27.0, `tree-sitter-python` 0.25.0 and
  `tree-sitter-typescript` 0.23.2 (identical to OXIDE's pins), `-javascript` 0.25.0,
  `-java` 0.23.5, `-go` 0.25.0, `-c` 0.24.2, `-cpp` 0.23.4. OXIDE runs
  py-tree-sitter 0.26.0, so the runtimes differ while the grammars match.
- The pins are ast-grep's, not OXIDE's: an ast-grep upgrade can change a grammar
  under OXIDE without any change to OXIDE's own pins (inferred from the manifest).
- `register_dynamic_language` loads other grammars from a compiled `.so`/`.dylib`/
  `.dll` (documented), which shifts the native build to OXIDE.

## Matching semantics (documented)

- Patterns must parse as valid code in the target language; a metavariable is
  usually parsed as an identifier, so operators and keywords can't be
  metavariables (`$L $OP $R` fails), and those cases need `kind` rules.
- `$A` matches one named node, `$$$A` a sequence. Strictness `cst`/`smart`
  (default)/`ast`/`relaxed`/`signature` controls which nodes may be skipped.
- Comments and string contents aren't code: a call text inside either doesn't match
  (measured).

## Positions

- `Pos.index` is a **character** offset: the binding converts bytes to chars
  (`py_node.rs` `byte_to_char`, documented), measured as 20 vs byte 22 after one
  `é`. OXIDE's `SourceRef` is in bytes, so any integration has to convert back.
- `SgRoot` takes `str`, so OXIDE's captured bytes have to be decoded. Invalid
  UTF-8 handling has to be chosen explicitly (inferred).

## Malformed source

Tree-sitter error recovery: on `def f(:` followed by `x.foo(2)` inside an `ERROR`
region, `foo(1)` matched and `x.foo(2)` didn't (measured). Matches in damaged
regions are silently missing. They aren't reported, so results on partial parses
are incomplete without any signal.

## Bounding and concurrency

- No timeout or cancellation, and the binding never releases the GIL (no
  `allow_threads`/`detach` in `crates/pyo3/src`, documented). A call runs to
  completion inside the caller's thread, so any bound has to come before the call:
  file count, bytes and match caps.
- Upstream reports one quadratic case: #2973, a 200-link method chain. It only
  affects debug builds (the reporter confirmed release builds are fine). #2800
  (open) asks for per-rule timing because rules can be slow on pathological files.

## Correctness history relevant to Python/TypeScript (upstream issues)

Closed in 2025–2026: `except` not matchable as a pattern keyword (#2182, needs a
`kind` rule), line continuations preventing Python matches (#2815), named nodes
dropped by `pattern` (#2856), comments treated as parameters (#2206), escape
sequences in strings changing `pattern:` (#2847). Pattern semantics for Python are
still being refined release to release.

## Agent ecosystem

- ast-grep ships an official agent skill (`ast-grep/agent-skill`). Its workflow is:
  write example code, write a rule, test it on the example, then run
  `ast-grep run -p … --lang …` or `ast-grep scan --inline-rules … --json` over the
  repository.
- There's also an experimental official MCP server (`ast-grep/ast-grep-mcp`) with
  AST dump, rule testing and search tools.
- Agents can therefore get structural search without OXIDE. OXIDE could only add
  things the CLI lacks:
  - the indexed snapshot's exact bytes,
  - mapping each match to OXIDE entity keys and relations,
  - a bounded, typed result in the existing API/MCP contract (inferred).

## Relation to the earlier decision

Request-time ast-grep (Rust `ast-grep-core`, `src/structural.rs`) answered
"callers of X" and "implementors of X". Precomputed relations replaced it after
parity (`docs/precomputed-relations-migration/`). The Python engine now stores
`calls` (resolved, ambiguous and unresolved, with the name and evidence span),
heritage `references`, `imports` and `tested_by`. A new evaluation must not
recreate callers or implementors search. The only open question is **shape**
queries, which have no entity/relation equivalent: argument patterns, empty
`except`, missing keywords, nesting constraints.

## Questions this leaves for the experiment

1. Do agents issue shape-level searches often enough to matter? The Codex
   transcripts in `oxide-agentnav/runs` (322 runs) are the measurable source.
2. On those shapes, how much more precise is an AST match than the regex an agent
   would write?
3. Does OXIDE's existing tree-sitter `Query` give the same matches with no new
   dependency? If so, ast-grep's remaining advantage is pattern ergonomics for
   agents, and the agent skill and CLI already offer that outside OXIDE.
4. Costs at scale: full-repo latency and RSS on VS Code (3,903 TypeScript files),
   45 MB installed, and wheels for every supported platform. PyPI lists cp312–cp314
   wheels for manylinux_2_28 x86_64/aarch64, macOS 10.12 x86_64, macOS 11 arm64 and
   win_amd64. Only Linux is executable here.
