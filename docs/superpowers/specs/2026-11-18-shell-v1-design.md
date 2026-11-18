# Bash / POSIX sh v1 language slice — intent spec

## Intent

Add a conservative `bash` / POSIX `sh` slice to OXIDE v0.2.0. It produces function
and variable declarations, literal command references, `source`/`.` imports and
script-interpreter invocations as entities and typed relations, with the existing
four-way evidence labels. Everything stays reachable through the existing
`search`, `context`, `outline`, `inspect`, `refs`, `impact` and single-MCP-tool
surface; no new MCP tool, no embeddings, no judges, no network at index time and
**no execution of indexed scripts**.

## Goals

- Shell-aware file detection: `.sh`, `.bash`, `.bashrc`, `.bash_profile`,
  `.bash_login`, `.bash_logout`, `.bats`, plus shebang detection for extensionless
  and ambiguous files. Precedence is shebang > extension > default (`.sh` → POSIX
  sh, `.bash`/`.bats`/`.bashrc`/… → Bash); extensionless, shebang-less files are
  unsupported. Distinguish Bash-only syntax from POSIX sh.
- Extract function definitions (`f() {}` and `function f {}`), assignments
  (`X=v`, `export`, `readonly`, `declare`, `typeset`, `local`), literal command
  names, `source`/`.` paths, `bash|sh|dash <script>` invocations and simple
  `$VAR` / `${VAR}` expansions, including inside nested `if`/`for`/`while`/
  `case`/subshell/command-substitution/pipeline constructs. Keyword declarations
  are not also emitted as command calls; options-only forms declare nothing.
  Positional/special parameters and modifier expansions (`${V:-d}`) are excluded.
- Byte-accurate source ranges for every fact.
- Conservative cross-file resolution: functions by name; sourced scripts only
  when the path is statically determinable and resolves to a captured file;
  interpreter invocations to captured scripts. Ambiguous and dynamic references
  are emitted with their written name and span, never as proven edges.

## Non-goals / anti-goals

- No aliases, `eval`, `command`/`builtin` dispatch, PATH lookup, shell option
  (`set -e`, `shopt`) semantics, runtime variable values, arithmetic expansion,
  dynamic command names, glob expansion of source paths, or emulating a shell.
- No new relation kinds, entity categories, evidence fields or MCP tools.
- No changes to other languages, frozen baselines or `FACTS_FORMAT` semantics for
  existing dialects.

## Constraints

- Pin `tree-sitter-bash==0.25.1`; keep `tree-sitter>=0.25,<0.27`. Wheels exist
  for CPython 3.10+ (abi3) on macOS x86-64/arm64, manylinux x86-64/arm64,
  musllinux x86-64/arm64, Windows amd64/arm64 — covering OXIDE's 3.12–3.14 rows.
- Every `src/oxide/**` file stays ≤500 LOC.
- Entity keys: `symbol:<path>#<name>` (globals) and
  `symbol:<path>#<function>.<name>` (locals); duplicates get a source-order
  `~n` ordinal. Files are `file:<path>`.
- Evidence uses the shared model: proven = `resolved`/`resolved`, heuristic =
  `resolved`/`heuristic`, ambiguous = `ambiguous`/`syntactic`, unresolved =
  `unresolved`/`syntactic`.

## Approaches

1. **Mirror the PHP slice (chosen).** A query-driven extractor produces a
   cacheable `Parsed`; a facts codec; a resolver indexes all captured shell
   files and emits the common `Entity`/`Edge` shapes. Trade-off: one new
   language module family and no navigation changes, consistent with every
   other language slice.
2. **Reuse the C scope machinery.** Rejected: shell has lexical-ish locals but
   dynamic globals, no types or overloads; the C scope tree would imply
   semantics the grammar does not support.
3. **Regex/line-based extraction.** Rejected: loses byte spans, cannot handle
   nesting or quoting, and would violate the grammar-backed contracts.

## Resolution ladder

1. `source`/`.` with a static path containing a slash, resolved against the
   containing file's directory to a captured file → `file:<path>` (proven).
   Slashless names are PATH-dependent and absolute paths are unverifiable: both
   are unresolved. Flags before a script (`bash -e foo.sh`) are skipped.
2. `bash|sh|dash <static script>` → `file:<path>` (proven), same path rules.
3. A literal command name is resolved only against functions defined in the
   **reachable set** of the calling file: the file itself plus files reachable
   by a transitive chain of proven `source`/`.` edges (cycle-guarded). Exactly
   one candidate → proven; several → ambiguous (all candidates); none →
   unresolved. A name matching a function in an unrelated, never-sourced file
   is *not* a candidate, so no false proven cross-file edge is created. A
   literal path to a captured script is a proven file-level call target.
4. Variable expansion matching locals of the enclosing function first, then
   file-globals; several → ambiguous; none → unresolved. Only `local` creates a
   function-scoped variable: a plain assignment inside a function is global in
   shell and stays file-level. No cross-file variable edges (they cannot be
   proven without execution).
5. Dynamic source paths and dynamic command names → unresolved, written name
   and span retained; never a proven edge.
6. This slice emits proven / ambiguous / unresolved only; it never produces a
   heuristic label, because no name match is treated as evidence.

Command names and import paths are carried as unresolved/ambiguous relations on
the existing `file:`/`symbol:` categories; no new entity category is introduced.

## Bash vs POSIX sh

Both dialects share one grammar and one facts codec; `Parsed.dialect` records the
detected dialect. The dialect affects only diagnostics/coverage — extraction,
relation kinds and resolution are identical for both. Bash-only constructs
(arrays, `[[ ]]`, `(( ))`, `function` keyword, `declare`/`local`/`typeset`,
`source`, process substitution, `select`, C-style arithmetic/loops) are recorded
as diagnostics in POSIX files so coverage is `partial` rather than a false claim
of complete POSIX conformance. `.` is accepted in both dialects. N same-name
definitions yield N candidates and therefore an ambiguous edge; same-scope
duplicates get an internal source-order `~n` ordinal so public qualified names
stay stable, and local keys include the enclosing function's ordinal too.

## Evidence / validation

- `bash -n` and ShellCheck (SC1090/SC1091) are independent syntax/static-analysis
  references only; graph correctness is asserted by deterministic conformance
  tests, never by those tools. They run only in developer/CI test scripts, never
  from `oxide.capture`, `oxide.core` or `index()`. The shell fixture is isolated:
  no frozen `fixtures/eval/` artifact or conformance golden is regenerated.
- Deterministic fixture `fixtures/shell_repo`, cross-file tests and
  `tests/python/test_shell.py`; navigation test in `test_navigation.py`.
- Benchmarks: `scripts/shell_measure.py` for indexing/retrieval; wheel download
  smoke across target platforms/Python versions.
- Full `mise run verify` + `mise run tier` and an independent zero-context
  review before acceptance. Do not publish or retag v0.2.0 without approval.
