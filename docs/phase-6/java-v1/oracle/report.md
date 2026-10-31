# javac oracle on gson (measured)

gson 854c8255b625cf1e13c701a83ea9ccb4caaa576a. JDK: Temurin 25.0.5+8 (Adoptium tarball, SHA-256 verified), `javac 25.0.5`. `Oracle.java` (`java Oracle.java ROOT SHADOW OUT MODULES…`) runs `JavacTask.parse()` + `analyze()` only, with `-proc:none -implicit:none -XDshould-stop.ifError=FLOW -Xmaxerrs 1000000 -encoding UTF-8`. There is no classpath, so every dependency is an error symbol. Each Maven module is compiled separately: its `src/main/java` + `src/test/java` units, with `module-info.java` skipped.

`-sourcepath` lists every module's `src/main/java`, with two deviations from the brief: it is a symlinked mirror without `module-info.java` (a visible one switches javac to module mode), and it adds gson's `src/main/java-templates` (`GsonBuildConfig`, which OXIDE indexes).

`gson-uses.json` (5.97 MB, kept in scratch; SHA-256 `c9dd576a42aefaa647f2c2b2b41f7447e1aed780067e6ed67ff19b57a9870e70`): 260 units, 68,241 uses (40,409 in-repo, 15,753 JDK, 11,839 error symbols, 240 null or static-import).

| Module | gson | extras | metrics | proto | test-graal-native-image | test-jpms | test-shrinker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| units | 208 | 11 | 6 | 10 | 2 | 4 | 19 |
| javac errors (missing deps) | 5,989 | 180 | 192 | 673 | 136 | 132 | 118 |

`score.py` runs `capture` + `derive` (as `eval_java.py audit` does). For each edge, the query position is the last identifier of the evidence, after blanking type arguments, converted from bytes to UTF-16 units. An edge AGREEs when javac's declaration start lies inside a target's span (for a module target, in a file of that package). "Strict" additionally requires the innermost OXIDE symbol containing the declaration to be the target.

## Precision of resolved edges (after the annotation-scope fix)

| Kind | Agree | Agree (strict) | Disagree | No oracle | Precision |
| --- | --- | --- | --- | --- | --- |
| calls | 7,876 | 7,876 | 0 | 3 | **1.0000** |
| references | 5,710 | 5,710 | 0 | 0 | **1.0000** |
| imports | 1,085 | 1,085 | 0 | 0 | **1.0000** |
| overall | 14,671 | 14,671 | 0 | 3 | **1.0000** |

Before the fix, the same javac output scored references 5,709 / 1 (0.9998) and overall 14,670 / 1 (0.9999).
Every other count below is unchanged by the fix.

- **No-oracle reasons:** 3 `calls:error`. In `CircularReferenceTest` lines 57/75/101, javac cannot attribute `assertThrowsStackOverflow(...)` because its argument type comes from absent JUnit.
- **Ambiguous calls:** 2,078. For all 2,078, javac's declaration is one of the candidates (0 in none, final pass).
- **Unresolved, javac in-repo:** 1,079 (calls 134, references 945). By javac element kind: type parameter 874 (OXIDE has no type-parameter entity, by design), METHOD 92, CLASS 73, RECORD 40.
- **Imports:** all 1,589 unresolved imports are no-oracle (JDK or dependency); no in-repo import is unresolved.
- **Scorer self-check:** the scorer rejects 300/300 resolved calls whose target was swapped for a random other method.

## Unresolved sample: first 40 by SHA-256 of the edge, classified against docs/java.md "Not modeled"

| Cause | Count |
| --- | --- |
| other: type-parameter reference (`T`, `K`, `V`, `R`, `E`; no entity) | 36 |
| anonymous/local class: local records `LocalRecord` and `LocalRecordDeserialization` | 2 |
| anonymous/local class: unqualified calls to outer statics (`requireNonNullField`, `localDateTime`) from anonymous `TypeAdapter`/`TypeAdapterFactory` bodies, whose inherited members are unknown | 2 |
| chained call, var/lambda receiver, generic receiver, external supertype, field of a variable | 0 |

Type parameters dominate the sample. An extra seeded sample of 20 unresolved *calls* (scratch, not the brief's sample) shows outer-class statics called from anonymous bodies (`canonicalize`, `typeToString`, `upperCaseFirstLetter`), local classes and records (`new LocalRecord`, `new NullAdapter`), calls through a static field (`FormattingStyle.PRETTY.withIndent`) and `Object` members (`factory1.equals`).

## Every disagreement (final pass before the fix: 1; after: 0)

**OXIDE error, fixed.** `@JsonAdapter` at `gson/src/test/java/com/google/gson/functional/JsonAdapterAnnotationOnClassesTest.java:221` (on `private static class A`) resolves to the member class `A.JsonAdapter`. javac binds the imported `com.google.gson.annotations.JsonAdapter` (`gson/.../annotations/JsonAdapter.java`). OXIDE looks the annotation up in `A`'s body scope, but the annotations of a class declaration are resolved in its enclosing scope. The fix resolves a declaration's
annotations and a class's supertypes in the enclosing scope; re-scoring the same `gson-uses.json` turns this edge
into an agreement, and it is the only edge of gson that changes ([../verification/annotation-fix-diff.log](../verification/annotation-fix-diff.log)).

## First pass vs final

| Pass | calls precision | references | imports | overall | Ambiguous in none |
| --- | --- | --- | --- | --- | --- |
| 1 | 1.0000 | 0.9998 | 1.0000 | 0.9999 | 8 |
| final | 1.0000 | 0.9998 | 1.0000 | 0.9999 | 0 |
| after the fix | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |

**Scorer error (1 class, 8 edges).** For explicit type-argument calls (`gson.<Car>fromJson`), the first pass cut the evidence at the first `<` and queried the receiver `gson`. The fix blanks balanced `<…>` in place, and the rerun places all 8 among the candidates. I read every disagreement in both passes in full (1 each, the same edge) and every ambiguous-in-none edge (8).
