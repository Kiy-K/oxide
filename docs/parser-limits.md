# Parser limits: long chains and deep syntax

The ast-grep evaluation ([phase-6/astgrep-feasibility](phase-6/astgrep-feasibility/README.md))
found `oxide.core.python.parse` super-linear on long method chains: 3.0 s for 10k
`.then(f)` links, 29.6 s for 30k, over 120 s for 100k. This records the diagnosis, the
fix and the one explicit limit left (measured 2026-10-18, py-tree-sitter 0.26.0,
tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, Linux x86_64).

## Diagnosis

Two causes, both independent of the language slices' semantics.

1. **OXIDE: `Node.parent` and sibling lookups are O(depth).** tree-sitter stores no
   parent pointers. `ts_node_parent` and `prev_named_sibling` descend from the root to
   find a node's parent, so they cost the node's depth. A chain `p.then(f).then(f)…`
   nests two levels per link. Every call was read through `callee.parent`, which made
   the facts loop quadratic: at 8k links, 1.95 s of parent lookups against 13 ms of
   parsing and 8 ms of querying. The TypeScript slice had the same pattern in four
   places: callees, `variable_declarator` names, the `export`/`declare`/`const` wrappers
   of declarations, and member decorators. Declarations deep inside a chain
   (`p.then(() => { function g() {} })`) took 47 s at 20k links. One more path was
   quadratic after a deep nest: finding a byte's innermost scope walked up the scope
   tree one parent at a time (TypeScript, 8k nested functions then 60k calls: 27 s).
2. **tree-sitter: the query cursor fails below depth 65,535.** It keeps node depth in
   16 bits. Even a trivial `(identifier) @i` query goes from 0.02 s at 10k nested arrow
   callbacks to 2.3 s at 20k and 10.5 s at 30k. Past that depth matches are also
   **silently dropped**. A Python chain of 32,766 links (tree depth 65,534) yields all
   32,766 calls; 32,768 links (depth 65,538) and 100,000 links both yield exactly
   32,767, and the 100k query took 719 s. The `(MISSING)` pattern, which inspects every
   node, was most of the query time on deep trees (5.2 of 8.2 s at 20k callbacks).

## Fix

- Calls, declarators and wrappers are captured whole and read downwards
  (`child_by_field_name`), never through `Node.parent`. Wrapper parents and member
  decorators come from a node-id map built from the captured `export_statement`,
  declaration and `class_body` nodes.
- `Nesting` (`src/oxide/core/python.py`) finds the innermost scope with ancestor jump
  pointers in O(log depth). A randomized test checks it against the parent walk it
  replaced.
- `(ERROR)` and `(MISSING)` run as a second query, only when the tree has an error.
- **Explicit limit:** a file whose syntax tree is deeper than 65,535 levels is
  `unsupported` with the diagnostic `syntax nests deeper than 65,535 levels`, rather
  than silently incomplete and slow. The check (`too_deep`) enters only subtrees with
  enough nodes to reach that depth, so it costs nothing measurable on ordinary files.
  The file still has its file entity, retained source and lexical document.

This changes derived facts only for files past the limit, so the `python` and
`typescript` components and both facts versions were bumped, and ContextBench was
re-frozen for the version only (`baseline-v8`, `typescript-v5`: identical entries,
routes, payloads and metrics).

## Measured

| Input (single file) | Before | After |
| --- | --- | --- |
| Python chain, 30k links | 29.6 s | 0.12 s |
| Python chain, 100k / 130k links (over the limit) | > 12 min, 32,767 of the calls | 0.23 / 0.27 s, unsupported |
| TypeScript chain, 30k links | quadratic (2.3 s at 8k) | 0.13 s |
| TypeScript, 20k links each declaring a function | 47 s | 0.32 s |
| TypeScript, 8k nested functions then 60k calls | 27 s | 0.51 s |
| TypeScript, 20k / 100k nested arrow callbacks (over the limit) | 4.5 s query / not run | 0.18 / 0.75 s, unsupported |
| Python, 1,000 nested defs then 100k calls (912 KB) | 3.45 s | 0.65 s |

Old and new parsers produce byte-identical encoded facts for all 8,176 Python and
TypeScript files of the ten feasibility corpora (requests, flask, pytest, xarray,
seaborn, code-server, darkreader, vue core, MUI, VS Code), and for generated chains,
nests, decorators, JSX and broken files small enough for the old parser. Parsing VS
Code's 3,902 files took 7.4 s before and 7.0 s after.

## Not fixed

- The limit is tree-sitter's. Deep trees just under it still make the native query
  cursor slower than linear, but no case measured under the limit took more than
  1 s.
- py-tree-sitter 0.26.0's `QueryCursor` `progress_callback` segfaults the process
  as soon as it fires, even on a 2k-level tree. It cannot be used as a work budget.
- Structural search (`Repository.structural_search`) parses with ast-grep's own
  engine. That measured at most 5.1 s on 100k-deep inputs, and it still searches
  files this limit marks unsupported.
