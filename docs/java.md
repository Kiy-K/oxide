# Java source support

`oxide-java-v1` adds Java as a language slice of the existing engine:
`core/languages/java.py` extracts syntax facts with `tree-sitter-java==0.23.5`, and
`core/languages/java_resolve.py` resolves them over the whole capture. No JDK, `javac`,
build tool (Maven, Gradle), dependency download or repository code runs during
indexing or querying. The ordinary Python API, CLI, retrieval, graph, router and context
packer serve these files unchanged, and MCP remains exactly one tool, `oxide_search`,
with `search` and `context` modes.

## Extraction and identity

`.java` files report `FileInfo.language == "java"`. Declared types and their members
become symbols, nested as written:

| Java declaration | Kind | Key segment |
| --- | --- | --- |
| `class C` | `class` | `C` |
| `interface I` | `interface` | `I` |
| `enum E` | `enum` | `E` |
| `record R(...)` | `record` | `R` |
| `@interface A` | `annotation` | `A` |
| a method, abstract or interface method, or annotation element of `C` | `method` | `C.m` |
| a constructor of `C`, compact canonical constructors included | `constructor` | `C.C` |
| a member type `N` of `C` | its kind | `C.N` |

Keys are `symbol:<path>#<segment>`. A member's physical parent is its type, and a
top-level type's is the file. Overloads and other equal segments take `~n` in source
order (`Store.put`, `Store.put~1`), and members nest under the ordinal'd segment.
Entity names are the plain identifiers. Not symbols: fields, enum constants, initializer
blocks, local classes (declared in a method body) and anonymous classes. Their code
stays in the enclosing symbol, and calls inside them come from it.

A declaration's range is its tree-sitter node: modifiers and annotations included,
Javadoc excluded. Its signature is the header up to the body, without annotations, with
whitespace collapsed (`public class Store extends AbstractStore implements Backend`,
`public String get(Key key)`, `int seconds() default 60`).

A package is every `.java` file with the same package declaration, wherever its
directory: `module:java:<package>`, named by the package, so `src/main/java` and
`src/test/java` halves of one package are one module. Modules `contain_logical` their
files and `define` their top-level types. Default-package files form one package per
directory for resolution and have no module.

Test methods are methods annotated `@Test`, `@ParameterizedTest`, `@RepeatedTest`,
`@TestFactory` or `@TestTemplate`, matched by simple name, so the annotations of JUnit 4,
JUnit 5 and TestNG all count, qualified or not. A resolved call made inside a test,
including its lambdas and anonymous classes, adds `tested_by`, the same heuristic as the
other slices.

## Resolution

Everything below follows the Java Language Specification's scoping and javac's
compile-time lookup, applied to the captured source only. Nothing runs a type checker.
A lookup that would need one, or that reaches code outside the capture, is unresolved
rather than guessed.

- **Types.** A simple type name resolves in javac's order: type parameters and local
  classes, which are opaque; member types of the enclosing classes, innermost first,
  including types inherited from captured supertypes; then the file's own top-level
  types, single-type imports, types of the same package and on-demand imports. A
  single-type import of something not captured (`import java.util.List;`) still shadows
  the package, so the name is unresolved. Several on-demand imports offering a name
  are ambiguous. A qualified name (`a.b.Box.In`) resolves through its longest captured
  package prefix, or through a type and its member types.
- **Imports.** Each import is an `imports` edge from its file to what it names: the type
  of a single-type import, the package's module (or the type) of an on-demand import, and
  for a static import the methods and member types of that name (every overload, so an
  overloaded name is ambiguous), or the owner type for a field.
- **Methods.** A call `m(...)` resolves to the innermost enclosing class that has a
  member method `m`. Its members are the methods it declares plus those inherited
  through captured supertypes, the nearest declaring type first. Then come
  single-static-imported and static-on-demand-imported methods. Overloads are filtered
  by argument count; varargs methods accept their arity minus one or more. Several
  that fit are ambiguous. `this.m()`, `super.m()`, `Type.m()` and `Type.FIELD.m()`
  resolve against that type. `x.m()` and `this.x.m()` resolve against the declared type
  of `x`: a parameter, local, pattern, resource, catch or loop variable, or a field of
  the enclosing classes or their captured supertypes.
- **Calls** cover method invocations through an identifier, `this`, `super`, a variable,
  a type or `this.field`, and instance creations `new T(...)`, which call the class
  (anonymous class bodies included).
- **References** cover every class or interface type written in a declaration: supertypes,
  fields, parameters, returns, `throws`, locals, casts, type arguments and bounds. They
  also cover annotations (`@Cached`).
- **Declaration headers.** As in javac, a declaration's annotations and a class's
  type-parameter bounds, `extends`/`implements` types and `permits` clause are looked up
  in the scope enclosing it: a class's own member types do not shadow them
  (`@JsonAdapter class A { class JsonAdapter {} }` names the imported annotation, and
  `class A<T extends B> { class B {} }` names the outer `B`). The class's type parameters
  stay visible in its header, and the edges stay the declaration's own. A record's
  components are not header: they see its member types.
- **Conservative locals.** A variable's declared type is kept only when every binding of
  its name in the scope has the same type. A name bound with `var`, a primitive or array
  type, a lambda parameter or several different types is opaque, and so is any name a
  variable shares with a type (javac's variable-obscures-type rule). Calls on opaque
  receivers are unresolved.
- **Unknown members stop the lookup.** If the class holding the call, or one of its
  supertypes, is outside the capture (`extends JPanel`), it may declare the name. So do
  a local or anonymous class body and an enum constant's body. In each case the call is
  unresolved instead of falling through to an outer class's method of the same name.
  The same holds for names every class has implicitly: `Object`'s methods, an enum's
  `values`/`valueOf`/`name`/`ordinal` and the like, and a record's component accessors.

Same-named top-level types in several files of one package (duplicate sources, generated
and handwritten variants) make a reference ambiguous, listing every candidate.

## Not modeled

These need type inference, a classpath or a build model. They stay unresolved (with
their name) or absent rather than guessed:

- calls on expression values: chained calls (`a.b().c()`), fields of variables
  (`x.f.m()`), array elements, casts, `Outer.this.m()` and `outer.new Inner()`;
- the receiver types of `var` locals and lambda parameters, generic type arguments
  (`List<Store> l; l.get(0).put()`), and type-parameter bounds;
- overload resolution by argument type. Arity is the only filter, so equal-arity
  overloads are ambiguous. A nearer declaring type with a fitting arity wins over
  an inherited overload of the same arity, which javac might prefer by parameter type;
- dynamic dispatch: a call resolves to the compile-time declaration (`Backend.get`), not
  to overriding implementations, and there is no `implements` relation;
- member types and fields inherited from supertypes outside the capture: they are assumed
  absent, so a name such a supertype declares can resolve to a same-named outer or
  package type (fields: a receiver named like a type resolves as that type);
- method references (`Foo::bar`), explicit constructor invocations (`this(...)`,
  `super(...)`), enum constants, fields, initializers and value uses as relations;
- the JDK and every dependency, module-info (`module` declarations are not symbols),
  annotation processors and generated sources that are not captured, and build-time
  source sets (Maven/Gradle are not read; `pom.xml` and `build.gradle` are file evidence);
- JUnit 3 `TestCase` naming conventions and test frameworks without the annotations above;
- the type-checked validity of the program: a call a compiler would reject may resolve.

Structural search (`Repository.structural_search`, ast-grep) remains Python and
TypeScript only.

## Reliability

The shared bounded native query, downward-only node reads, depth limit (65,535), UTF-8
check and bounded syntax diagnostics apply unchanged. Invalid UTF-8 or excessive depth
is unsupported file evidence. Grammar recovery is partial and keeps recovered
declarations. Resolution always reruns over the full capture, so a change in one file
re-resolves its dependants with every other file's facts reused.

Facts are cached by `(java, source digest)` under `oxide-java-facts-v1`. The cache
decoder validates types, ranges, identifier and dotted names, and the scope tree,
including that members belong to declared types. Anything else is parsed again.
Derivation components add `java` (`oxide-java-v1`) and `java-parser`, and older indices
are re-indexed. Evidence and reproduction: [phase-6/java-v1](phase-6/java-v1/README.md).
