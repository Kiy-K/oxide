# Java feasibility spike

Cheap grammar/extraction pass only, per the plan's Phase 3. No Java code
shipped at the time; `tree-sitter-java` was added, probed, and removed again.

**Original verdict: NO-GO. Superseded — Java shipped.** See
["Resolution"](#resolution-the-blocker-was-real-the-fix-was-not-the-one-named)
at the bottom: the blocker this spike found was real, but the fix it proposed
(changing symbol identity for every language) was not the only one available,
and the one actually taken cost nothing outside Java.

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


---

## Resolution: the blocker was real, the fix was not the one named

Java shipped. The overload collision this spike found was exactly as
described, and it was closed without touching any other language's symbol
identity.

**What this document got wrong.** It said:

> The fix is to put parameter types into the qualified name. That changes
> symbol id composition, which AGENTS.md pins as load-bearing — and because
> `Symbol::id` is shared across all languages, changing it invalidates every
> stored embedding in every existing index, not just Java's.

The first sentence is right; the second conflates two different things. Symbol
id *composition* is the formula `FNV1a(file + \0 + qualified_name)`. Putting a
signature into a Java method's `qualified_name` does not change that formula —
it changes one input, for one language, on symbols no existing index contains,
because no existing index has ever held a Java symbol. Nothing outside Java
moves, so nothing outside Java re-embeds.

That is now asserted rather than argued: the five pre-existing conformance
goldens (`fixtures/conformance/{python,typescript,tsx,rust,go}.golden.json`,
which record every symbol's `id` and `content_hash`) are byte-identical across
the change, and `language_conformance.rs::java_overloads_keep_distinct_ids_
without_touching_other_languages` additionally asserts that no non-Java
qualified name anywhere contains a `(`.

**What shipped.** `tags.rs::java_signature` normalizes each parameter the way
the JVM's own overload rules do, and `collect_meta` records it per
method/constructor declaration:

| Declaration | Qualified name |
|---|---|
| `String get(String key)` | `Store.get(String)` |
| `String get(String key, String fallback)` | `Store.get(String,String)` |
| `String get(byte[] raw)` | `Store.get(byte[])` |
| `void put(final String k, @Nullable String v, int... flags)` | `Store.put(String,String,int[])` |
| `<T> List<T> all(List<T> in, java.util.Set<String> keys)` | `Store.all(List,Set)` |
| `Store(Map<String,String> data)` | `Store.Store(Map)` |

Generics are erased (two overloads differing only in a type argument are
illegal in Java, so keeping it would split one symbol in two), package
qualifiers reduce to the last segment (`java.util.Set` and an imported `Set`
are one type written two ways), varargs become arrays (`f(int...)` and
`f(int[])` cannot coexist), and parameter *names*, `final` and parameter
annotations never appear — so renaming a parameter never re-embeds a symbol.

**The other four points in this document.** All closed as predicted, none of
them a blocker:

2. `extends`/`implements` got OXIDE's own `java_implementors.scm`, capturing
   the individual type nodes rather than the shared `type_list` range.
3. Annotations needed *no* work at all — better than this document expected.
   Java's grammar puts `modifiers` (which holds annotations) inside the
   declaration node, so `@Service` is already inside the tag's byte range and
   therefore inside the symbol's span, hash and signature. No equivalent of
   `decorator_extended_start` was needed, and the class declaration's own line
   already matches the symbol's `start_line`, which is what lets
   `structural_relations` attribute a heritage clause.
4. Inner and nested classes nest on byte-range containment with no extra work,
   as predicted. Anonymous classes are still skipped.
5. Qualified names stay file-scoped; the package declaration is still not
   captured.

**Still not extracted** (deliberate, not blockers): fields, annotation-type
elements (`String value();` inside an `@interface`), and method references
(`Foo::bar`, whose grammar gives two identifiers with no field distinguishing
receiver from member, so the callee would be a guess). Upstream's `tags.scm`
was replaced outright by an OXIDE-owned `java_tags.scm` rather than extended.

**Measured.** Gson at `8b4b5505` (264 files, 4,388 symbols): cold index
2,010 ms, no-change 80 ms, one-file edit 270 ms, peak RSS 40 MB, index 26 MB.
The edit reported `2 new, 1 changed, 3 written, 4,385 reused` — incremental
re-embedding holds for Java exactly as it does for the other languages.
