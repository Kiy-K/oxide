# Java feasibility spike

Cheap grammar/extraction pass only, per the plan's Phase 3. No Java code
ships; `tree-sitter-java` was added, probed, and removed again.

**Verdict: NO-GO for now.** One blocker, named below. Everything else is
ordinary `.scm` work of the same shape Rust and Go already needed.

## Does the grammar build?

Yes. `tree-sitter-java 0.23.5` compiles against the pinned `tree-sitter`
0.27 with no version bump and no change to any other dependency —
`Parser::set_language` succeeds and `TagsConfiguration::new` accepts its
`queries/tags.scm` unmodified.

## What upstream `tags.scm` covers

Seven patterns, the thinnest of any language tried so far:

| Construct | Tagged? |
|---|---|
| `class_declaration` | yes (`@definition.class`) |
| `interface_declaration` | yes (`@definition.interface`) |
| `method_declaration` | yes (`@definition.method`) |
| `method_invocation` | yes (`@reference.call`) |
| `superclass` / `type_list` | yes, as `@reference.*` |
| `constructor_declaration` | **no** |
| `field_declaration` | **no** |
| `enum_declaration` | **no** |
| `record_declaration` (16+) | **no** |
| `annotation_type_declaration` | **no** |
| imports / package | **no** |

Probed against a small file with a package, imports, an interface, an
annotated class with a superclass and two interfaces, a constructor, two
overloaded methods, an inner class and a nested enum. The constructor, both
fields, and the nested enum produced no tag at all.

Every one of those gaps is closable the way Rust's and Go's were — an
OXIDE-owned pattern appended to the query, plus imports in `collect_meta`.
None of them is the blocker.

## Java-specific relation ambiguities

**1. Overloads collide on `(file, qualified_name)` — this is the blocker.**
The probe's `Store` declares `get(String)` and `get(String, String)`. Both
are tagged; both normalize to the qualified name `Store.get`; and
`parser.rs::parse_file_with`'s dedup — load-bearing, and deliberately so —
keeps the first and drops the second without a word.

In Python and TypeScript that dedup is a safety net for a rare shape
(conditional defs, `.d.ts`-style signature overloads). In Java, overloading
is idiomatic and pervasive, so the same rule becomes routine, silent data
loss: a builder class with six `of(...)` overloads indexes as one symbol.

The fix is to put parameter types into the qualified name. That changes
symbol id composition, which AGENTS.md pins as load-bearing ("Never change
id composition casually") — and because `Symbol::id` is shared across all
languages, changing it invalidates every stored embedding in every existing
index, not just Java's. That is a deliberate decision with a migration
attached, not something to slip in alongside a new language.

**2. `extends` and `implements` need OXIDE's own query.** Upstream tags both
as references, and `type_list` reports one match per name at the *clause's*
range (the probe shows `Backend` and `Cloneable` sharing byte range
202..220), so an implementors query must capture the individual
`type_identifier`s itself. Same shape as the TypeScript implements clause.
Not a blocker.

**3. Annotations are not decorators.** `decorator_extended_start` keys on the
node kind `decorator`; Java uses `annotation` / `marker_annotation`. One
extra node kind in `collect_meta`, so `@Service` and `@Override` join the
symbol's span the way `@app.route` and `@Injectable()` do. Not a blocker.

**4. Inner, nested and anonymous classes.** Inner and nested classes nest
correctly on byte-range containment already — the probe's `Store.Inner` and
`Store.Inner.ping` come out right with no extra work. Anonymous classes
(`new Runnable() { ... }`) have no name node, the same shape as the
TypeScript anonymous class expression that is already skipped rather than
misattributed. Not a blocker.

**5. Package-vs-file qualified naming.** The package declaration is not
captured and OXIDE's qualified names are file-scoped, so two `Store` classes
in different packages are distinguished by path alone. That is exactly how
OXIDE already treats Python modules. Not a blocker.

## What would make it a GO

Decide the overload question first, on its own: either accept the loss
explicitly for Java (and say so in the support matrix), or change symbol
identity to include a parameter signature and pay the full re-embed across
every language. Until that is settled, adding Java means shipping a language
whose most common construct is silently truncated.
