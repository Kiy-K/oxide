# PHP language slice (`oxide-php-v1`)

PHP is indexed with `tree-sitter-php==0.25.1`, pinned like the other grammars. The
slice uses the **full** grammar (`tree_sitter_php.language_php()`), which parses
inline HTML as `text` and PHP regions between `php_tag`/`php_end_tag`; PHP-only and
mixed `.php`/`.phtml` files therefore take the same path. The PHP-only grammar is
not used: pre-extracting PHP fragments would lose byte spans into the captured file.

## Gap analysis (CAP-003)

A query-driven extractor collects declarations, imports, bases, references and
calls; Python only assigns each captured node to its innermost declaration and
namespace. Unlike C#, PHP namespaces are regions (an unbracketed `namespace N;`
governs the rest of the file), so the facts model uses a flat `Decl.parent` plus a
`namespaces` region list rather than a scope tree. This is the one deliberate
deviation from the C# shape, needed to keep byte-accurate lexical scoping.

## Extracted facts

Kinds: `function`, `method`, `class`, `interface`, `trait`, `enum`, `constant`,
`case`, `property`. Namespaces (bracketed and unbracketed) produce module entities
`module:php:<dot-namespace>` and `contains_logical` edges. Imports cover class,
function and constant `use`, aliases and group `use`. Bases cover `extends`,
`implements` and a class body's trait `use`.

## Resolution ladder

A name is resolved against captured source only, in this order:

1. a leading `\` — exact fully qualified name → **proven**;
2. a `use` alias (explicit, or the trailing segment of an unaliased import) →
   its target → **proven**;
3. the current namespace + name, unique → **proven**, several → **ambiguous**;
4. the global name, unique → **heuristic** (PHP falls back to global functions
   and classes; this is a guess, not evidence);
5. Composer PSR-4/PSR-0 mapping to a captured file → **proven** file-level edge;
6. nothing found → **unresolved**, retaining the written name and byte span.

Composer `classmap` and `files` are recorded but are not used to invent symbol
edges: they have no reliable FQN→symbol mapping without running Composer's scan.

Method calls are resolved only when the receiver is statically known: `self::`,
`static::`, `parent::`, `Class::method()` and `$this->method()`. Inheritance is
followed through captured `extends`/`implements` with a cycle guard. Every other
receiver needs the value's type and is skipped.

## Not modeled

No edges for dynamic calls: variable functions (`$fn()`), variable method names
(`$o->$m()`), `call_user_func*`, `forward_static_call*`, magic `__call`,
reflection, service containers or string class names. Calls through an expression
(`SomeFactory::make()->run()`) are not resolved; the statically named part gets an
unresolved edge and the chained call is not recorded. No runtime autoloader is
emulated, no PHP or Composer code is executed, and `vendor/` trees are ordinary
captured files (indexed if present, with no special vendor semantics).

## Evidence model

Relations use the shared `resolution`/`basis` fields; `lookup` labels them
proven (`resolved`/`resolved`), heuristic (`resolved`/`heuristic`), ambiguous
(`ambiguous`/`syntactic`) and unresolved (`unresolved`/`syntactic`). No new field
or MCP tool is added. Navigation runs through the existing `search`, `context`,
`outline`, `inspect`, `refs` and `impact` APIs.
