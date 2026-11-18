# Bash / POSIX sh language slice (`oxide-shell-v1`)

Shell files are indexed with `tree-sitter-bash==0.25.1`, pinned like the other
grammars. Bash is a superset of POSIX sh, so one grammar and one facts codec
serve two `FileInfo.language` values: `bash` and `sh`. **No indexed script is ever
executed** — `bash -n` and ShellCheck are developer/CI references only.

## Detection

A shebang wins over the filename; the filename decides when there is no shebang.
`bash` in a shebang, or `.bash`, `.bashrc`, `.bash_profile`, `.bash_login`,
`.bash_logout`, `.bats` → Bash. `sh`, `dash`, `ash` or `ksh` in a shebang, or
`.sh` → POSIX sh. Extensionless, shebang-less files are unsupported. The dialect
affects only diagnostics/coverage: extraction, relation kinds and resolution are
identical for both.

Bash-only syntax in a POSIX file (`[[ ]]`, `(( ))`, arrays, `function`, `source`,
`declare`/`local`/`typeset`, process substitution, `select`, C-style loops) is
reported as a diagnostic so coverage is `partial`, never a false claim of full
POSIX conformance. `.` is accepted in both dialects.

## Extracted facts

- **Functions** from `f() {}` and `function f {}`, including definitions inside
  `if`/`for`/`while`/`case`/subshells/command substitutions. Shell functions are
  global once defined, so every one is a file-level symbol.
- **Variables** from `X=v`, `+=`, and `export`/`readonly`/`declare`/`typeset`/
  `local`. `local` inside a function is a child of that function's symbol;
  everything else is file-level. Options-only declarations declare nothing.
- **Commands** with a statically literal name: `word`, single-quoted
  `raw_string`, numbers, or a double-quoted string with a single `string_content`.
  `concatenation`, `$x`, `${x}`, `$(…)` and process substitution are dynamic.
- **Imports** from `source` and `.` with a static path containing a slash;
  **script calls** from `bash|sh|dash <script>` with such a path. Flags before a
  script are skipped, and `sh -c '<string>'` produces no path edge. A quoted but
  expanding path (`bash "$S"`) is an unresolved `calls` edge retaining its text.
- **Variable uses** from plain `$x`/`${x}`. Positional/special parameters (`$1`,
  `$@`, `$?`) and modifier expansions (`${x:-y}`) are excluded.

All facts keep half-open byte spans. Same-scope duplicate declarations get an
internal source-order `~n` ordinal so `symbol:<path>#<name>` keys stay unique.

## Resolution ladder

1. `source`/`.` or an interpreter invocation with a static relative path that
   names a captured file → `file:<path>` (**proven**). Paths are resolved against
   the containing file's directory — the fixture convention, not a runtime CWD
   claim. `..` that escapes the repository, absolute paths and slashless names
   (which invoke PATH lookup) are never resolved.
2. A literal command name resolves only against functions in the calling file's
   **reachable set**: the file itself plus files reached by a transitive chain of
   proven `source`/`.` edges (cycle-guarded). One candidate → proven; several →
   ambiguous; none → unresolved. A function in an unrelated, never-sourced file
   is not a candidate. A static path to a captured script is a proven file-level
   call target.
3. A variable expansion resolves against the enclosing function's locals, then
   the file's globals. No cross-file variable edges: sourced values depend on
   execution order.
4. Dynamic names and slashless source paths are emitted **unresolved** with their
   written text and span; nothing is invented. Executing `bash other.sh` does not
   import that script's functions, so it does not extend the reachable set.

The slice emits proven / ambiguous / unresolved only — no heuristic label.
Shell functions shadow external commands in the shell, but an external binary is
not a captured symbol, so an unmatched command is simply unresolved.

## Not modeled

Aliases, `eval`, `command`/`builtin` dispatch, PATH lookup, `hash`, shell options,
`read`, arithmetic expansion, glob expansion of source paths, runtime variable
values, and the execution order of sourced files. No shell or script is run, and
no network access happens at index time.

## Independent references

- `bash -n` checks syntax only; fixture files must parse (`.bats` uses `@test`
  syntax and is excluded from that oracle).
- ShellCheck (`-f json1 -s bash`) reports SC1090/SC1091 for the fixture's dynamic
  source paths — a differential lint reference, not a graph oracle. Both tools
  run only in tests, never from `oxide.capture`, `oxide.core` or `index()`.

## Packaging

`tree-sitter-bash==0.25.1` ships `cp310-abi3` wheels (Python ≥3.10 stable ABI), so
one wheel covers the 3.12/3.13/3.14 rows. Verified against PyPI metadata:
manylinux2014 x86-64 + aarch64, musllinux_1_2 x86-64 + aarch64, macOS x86-64 +
arm64 and Windows amd64 + arm64. The installed pair is `tree-sitter==0.26.0` with
`tree-sitter-bash==0.25.1`. Runtime smoke checks in fresh isolated virtualenvs on
CPython 3.12 and 3.14 both constructed `Language(tree_sitter_bash.language())`
and parsed a shell file successfully; `mise run pythons` exercises the full CI
matrix.

## Measurements

`uv run python scripts/shell_measure.py` prints one deterministic JSON object for
`fixtures/shell_repo` (index/retrieval timings, entity and edge counts, evidence
labels and coverage). Observed on this run: 11 files (10 shell), 38 symbols,
71 relations (38 proven / 0 ambiguous / 33 unresolved), 9 complete + 1 partial +
1 unsupported; index ≈0.006 s, search ≈0.027 s, context ≈0.043 s. Timings are
environment-dependent; counts and labels are the stable gate.

`uv run python scripts/shell_eval.py [path]` applies the slice to a real checkout
(default `docs/cap-removal-eval`, accept an optional path) and cross-checks syntax
with `bash -n`. Observed on that corpus: 32 files, 5 shell files (all complete and
`bash -n`-accepted), 25 symbols and 365 relations (103 proven / 0 ambiguous /
262 unresolved). Unresolved relations are dominated by external commands, which
are not captured symbols; the slice never guesses an edge for them.

## Evidence model

Relations use the shared `resolution`/`basis` fields through the existing
`search`, `context`, `outline`, `inspect`, `refs` and `impact` APIs. No new field
or MCP tool is added.
