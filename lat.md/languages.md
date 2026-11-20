# Language slices

Each supported language is a deterministic slice under `src/oxide/core/languages/` that derives symbols and typed relations from source.

## Supported languages

Python, TypeScript/JavaScript, Go, Rust, Java, C, C++, C#, PHP and Bash/POSIX shell.

JavaScript is handled by the TypeScript slice. Per-language scope and limits are in `docs/<language>.md` and [[docs/parser-limits.md]].

## Shape of a slice

A slice parses with tree-sitter, emits declarations and references, and resolves names the way the language itself binds them.

Typical modules are facts or syntax (parsing), model, and resolve; Rust adds Cargo and macro handling, TypeScript reads `tsconfig`, PHP reads Composer autoload maps. Shared helpers live in [[src/oxide/core/languages/nesting.py]]. A slice is validated against the language's own tooling oracle where one exists (for example javac for Java, gcc for C).

## Unknown files

Files no slice understands still count as evidence with explicit coverage, so retrieval and packing can still surface them.
