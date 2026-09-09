; Upstream tree-sitter-go 0.25.0 queries/tags.scm (MIT license,
; https://github.com/tree-sitter/tree-sitter-go) plus OXIDE-owned additions
; at the bottom. Upstream's own coverage is unusually good — functions,
; methods, types, calls, and imports are all there — so the only changes are
; kind refinements and the package-level var/const capture upstream declares
; but never tags as a definition.
;
; Two things upstream tags cannot express, filled by the narrow
; `collect_meta` walk in tags.rs rather than by a query:
;   - a method's receiver type, which is what qualifies `Get` as `Store.Get`.
;     Go methods are top-level declarations, so the containment stack has
;     nothing to nest them under and two types with a `String()` method
;     collide on the bare name.
;   - struct-vs-interface: every `type X ...` carries the same syntax type
;     here, so an interface is reclassified from the parse tree.

(
  (comment)* @doc
  .
  (function_declaration
    name: (identifier) @name) @definition.function
  (#strip! @doc "^//\\s*")
  (#set-adjacent! @doc @definition.function)
)

(
  (comment)* @doc
  .
  (method_declaration
    name: (field_identifier) @name) @definition.method
  (#strip! @doc "^//\\s*")
  (#set-adjacent! @doc @definition.method)
)

(call_expression
  function: [
    (identifier) @name
    (parenthesized_expression (identifier) @name)
    (selector_expression field: (field_identifier) @name)
    (parenthesized_expression (selector_expression field: (field_identifier) @name))
  ]) @reference.call

(type_spec
  name: (type_identifier) @name) @definition.type

(type_identifier) @name @reference.type

(package_clause "package" (package_identifier) @name)

(type_declaration (type_spec name: (type_identifier) @name type: (interface_type)))

(type_declaration (type_spec name: (type_identifier) @name type: (struct_type)))

(import_declaration (import_spec) @name)

(var_declaration (var_spec name: (identifier) @name))

(const_declaration (const_spec name: (identifier) @name))

; --- OXIDE-owned ---
; Upstream lists var_declaration/const_declaration with a bare @name and no
; @definition capture, so package-level state is never a symbol. It is
; frequently exactly what a reader is looking for (`var DefaultClient = ...`).
(var_declaration
  (var_spec
    name: (identifier) @name)) @definition.constant

(const_declaration
  (const_spec
    name: (identifier) @name)) @definition.constant

; Interface method signatures. Upstream captures none, so `Backend.Get` —
; the exact name a reader searches for — was not a symbol. The enclosing
; `type_spec` is already a container, so the containment stack qualifies
; these without further help.
(interface_type
  (method_elem
    name: (field_identifier) @name) @definition.method)
