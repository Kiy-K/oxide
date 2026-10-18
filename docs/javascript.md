# JavaScript and JSX source support

`oxide-javascript-v1` extends the existing ECMAScript pipeline in
`core/typescript.py`. It uses `tree-sitter-javascript==0.23.1`, rather than accepting
TypeScript syntax in JavaScript. No Node, Bun, compiler, package manager or
repository code runs during indexing or querying. The ordinary Python API, CLI,
retrieval, graph, router and context packer serve these files. MCP remains exactly
one tool, `oxide_search`, with `search` and `context` modes; the structural MCP
trial's REJECT decision is unchanged.

## Extraction and identity

`.js`, `.jsx`, `.mjs` and `.cjs` use the native JavaScript grammar (which includes
JSX) and report `FileInfo.language == "javascript"`. Functions and generators,
async functions, function-valued declarators, classes, constructors, methods,
accessors, function-valued fields, named/default ESM declarations and exported
variables become symbols. Anonymous ESM defaults use `<default>`; direct CommonJS
function/class exports use `<export:NAME>` (`default` for `module.exports`). These
synthetic names cannot be lexical identifiers. Other unexported variables stay
module-level source evidence, as in the existing slices.

Nested declarations retain physical containment, signatures, byte-exact source
ranges, content digests and duplicate-name ordinals. Module keys are
`module:javascript:<full path including extension>`: `a.js`, `a.mjs`, `a/index.js`
and the TypeScript module `a` remain distinct. A root `index.js` has a module too.
Existing Python/TypeScript symbol and module identities are unchanged.

Calls, construction, capitalized/dotted JSX component uses, class heritage and
imports/re-exports produce the existing typed relations. Intrinsic JSX tags do not
create calls. JS/JSX test/spec filenames and `__tests__` directories participate in
`tested_by`, using the same resolved-call heuristic as TypeScript. Instance member
calls, `this`, computed/private members and mixin heritage stay unresolved or
unmodeled. Optional chaining, destructuring/default/rest parameters, modern
expressions and template literals parse normally; this is syntax understanding,
not type or runtime value inference.

## Local module resolution

These are static captured-source rules, not a claim to reproduce every Node or
bundler loader. All lookups stay inside the captured repository, preserve case,
and never consult installed packages or the live worktree.

- **ESM in JavaScript:** an explicit relative source path resolves that exact file.
  If a missing `.js`/`.jsx`/`.mjs`/`.cjs` path has exactly one corresponding captured
  `.ts`/`.tsx`/`.mts`/`.cts` source, it may resolve that source. An existing JS file
  wins over a TS substitute. Explicit `.ts`/`.tsx` paths can also name captured
  source. A TS logical module must contain one captured file for this JS bridge;
  merged TS modules remain unresolved. This models source interoperability, without asserting that a runtime
  can execute TypeScript.
- **Extensionless JavaScript:** a bundler-style local lookup resolves only a unique
  source among `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts`, `.cts`; only when
  none exists does it try `index.js/jsx/mjs/cjs/ts/tsx`. Competing candidates remain
  unresolved. A JSON target, unsupported source or directory `package.json` stops
  fallback. This extensionless rule is deliberately not Node ESM conformance.
- **TypeScript to JavaScript:** existing TS module resolution and tsconfig alias
  priority stay intact. A relative lookup with no known TS module can reach a JS
  source using the rules above. Bare tsconfig path/baseUrl file candidates can
  reach a unique captured JS file after the TS candidates. JS importers do not
  inherit tsconfig aliases. Bare packages, workspace links, package `exports`,
  `imports`, `main` and conditional exports remain unresolved. JS directory-index
  lookup through a bare TS alias is not added.
- **Exports:** existing named/default/namespace exports, local aliases, re-exports
  and stars use the shared resolver. Explicit exports win over stars; cycles,
  unknown stars, rebinding and ambiguity remain visible. A known module object
  alone is never treated as a callable JavaScript symbol.

## Bounded CommonJS

CommonJS globals are available in `.cjs`, and `.js`/`.jsx` without ESM syntax when
the nearest captured `package.json` is absent or has absent/`commonjs` `type`.
`.mjs`, ESM syntax, `type: module`, malformed package metadata and unknown `type`
values disable this resolution. A nearer package overrides an ancestor. Parse
facts remain path-independent; package interpretation happens during whole-capture
resolution, including when only metadata changes.

Supported forms are unconditional module-level literal `require` bindings:
`const api = require('./a.cjs')`, `const { f, g: alias } = require('./a.cjs')`;
`exports.f = f`, `module.exports.f = f`, `module.exports = f`, static object maps
`module.exports = { f, alias: g }`, and direct function/arrow/generator/class RHS
exports. Namespace members follow known named exports; a callable default follows
its declared symbol. ESM can import these known named/default exports; a default
CommonJS object exposes its statically known members. `require` of ESM/TypeScript
is deliberately unresolved. Escaped/dynamic specifiers, side-effect-only or nested/conditional requires,
`require(...).member` bindings and destructuring defaults/rest are not resolved.

Any shadowed/rebound `require` disables its inference. Shadowed `module`/`exports`,
export-object aliasing, nested/conditional assignments, computed keys, spread,
unknown RHS, repeated replacement/member assignments or other export-object uses
make the CommonJS export shape opaque. `exports = module.exports = f` and object properties called `default` are explicitly
unsupported: a property named `default` must not be confused with the exported module
value. A known module import can still be recorded, but its unknown callable
and member targets are not invented. Exported object maps with inline function
values are opaque (direct `exports.f = function` is supported).

## Reliability and limits

The shared bounded native queries, downward-only node reads, nesting index, depth
limit (65,535), UTF-8 checks and bounded syntax diagnostics apply unchanged. Invalid
UTF-8 or excessive depth is unsupported file evidence; grammar recovery is partial
and keeps recovered declarations. CommonJS exports from a partial file remain
opaque. Coverage describes syntactic extraction, not the fraction of names resolved.
Flow, Babel proposals outside this pinned grammar, framework single-file components,
and semantic validation of a program are outside this milestone. Named function
expression self-bindings and class-valued ordinary variable declarations are not
fully modeled; anonymous default generator expressions also retain module-level
source evidence. Heritage inside direct CommonJS RHS class expressions is not
emitted (declaration/default class heritage is). Block/callback-local declarations are conservatively opaque outside their modeled
declaration scopes; object-literal methods never become bare lexical bindings. This
can lose valid callback-local resolutions rather than leak them into outer exports.

Facts are cached by `(dialect, source digest)`; JS and JSX share the same grammar,
while TS/TSX remain separate. The cache decoder validates types, ranges, identities
and scope trees. The facts container adds `oxide-javascript-facts-v1`; derivation
components add the grammar and `oxide-javascript-v1`. Old indices require re-indexing
and old caches are safely discarded. Resolution always reruns over the full capture.
Persisted edit/rebuild equivalence, metadata-only changes, source deletion, grammar
changes, corruption and long chains are covered by tests.

Evidence, limitations and reproduction: [phase-6/javascript-v1](phase-6/javascript-v1/README.md).
