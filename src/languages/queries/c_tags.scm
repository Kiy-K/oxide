; OXIDE-owned C tags query, seeded from upstream tree-sitter-c 0.24.2
; queries/tags.scm (MIT license, https://github.com/tree-sitter/tree-sitter-c)
; but substantially rewritten — upstream tags five things and gets the kinds
; wrong for OXIDE's IR (a `typedef` and an `enum` share one syntax type, and
; a union is a "class").
;
; Functions are tagged on the `function_declarator`, not on the enclosing
; `function_definition`, and deliberately so: a return type can carry any
; number of pointer levels (`char **`), which no finite set of query
; patterns can enumerate, while `function_declarator` is always at a fixed
; depth *below* whatever wrapper the return type adds. The cost is that the
; tag's range covers only `name(params)`, with no body — `tags.rs` widens it
; back to the whole `function_definition` via `FileMeta::c_function_spans`,
; and the same walk records which declarators are bare *prototypes*
; (`FileMeta::c_prototypes`) so a forward declaration loses to the real
; definition instead of winning `parse_file_with`'s first-wins dedup with a
; one-line, body-free symbol.
;
; `body: (_)` on the struct/union/enum patterns is what keeps a *use*
; (`struct store *s;`) from being tagged as a definition of `store`.

(function_declarator
  declarator: (identifier) @name) @definition.function

(struct_specifier
  name: (type_identifier) @name
  body: (_)) @definition.class

(union_specifier
  name: (type_identifier) @name
  body: (_)) @definition.class

(enum_specifier
  name: (type_identifier) @name
  body: (_)) @definition.enum

(type_definition
  declarator: (type_identifier) @name) @definition.type_alias

(enumerator
  name: (identifier) @name) @definition.constant

; `#define` is not preprocessing — it is how a C API declares its constants
; and its inline helpers, and it is what a reader greps for. Tagging the
; declaration is a world away from expanding it, which OXIDE does not do.
(preproc_def
  name: (identifier) @name) @definition.constant

(preproc_function_def
  name: (identifier) @name) @definition.constant

; File-scope variables only — anchored under `translation_unit` so a local
; inside a function body is never a symbol. Both the initialized and the
; bare form, each with one pointer level, which is as far as a file-scope
; declaration realistically goes (`static char **argv_copy;`).
(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: [
        (identifier) @name
        (pointer_declarator declarator: (identifier) @name)
      ])) @definition.constant)

(translation_unit
  (declaration
    declarator: [
      (identifier) @name
      (pointer_declarator declarator: (identifier) @name)
    ]) @definition.constant)
