; TypeScript/TSX symbol tags. Two upstream files concatenated plus two tiny
; OXIDE-owned additions at the bottom. Concatenation is required, not
; incidental: tree-sitter-typescript's own tags.scm (queries/tags.scm in
; tree-sitter-typescript 0.23.2, MIT) only covers TS-only surface —
; function_signature/method_signature/abstract_method_signature/
; abstract_class_declaration/module(namespace)/interface_declaration plus
; @reference.type/@reference.class. It does NOT cover class_declaration,
; function_declaration, method_definition bodies, or arrow-function consts —
; those live in tree-sitter-javascript's tags.scm (0.25.0, MIT), which the
; TypeScript grammar extends syntactically. Verified empirically: TS's
; tags.scm alone produced 0 tags for fixtures/ts_repo/src/index.ts and missed
; the entire AuthService class in auth/service.ts; concatenated with JS's it
; recovers classes, methods (with adjacent-JSDoc @doc capture), and functions.
; Both grammars share the same underlying node kinds for these constructs, so
; the JS patterns match cleanly against LANGUAGE_TYPESCRIPT/LANGUAGE_TSX trees.

; --- tree-sitter-javascript 0.25.0 queries/tags.scm ---
(
  (comment)* @doc
  .
  (method_definition
    name: (property_identifier) @name) @definition.method
  (#not-eq? @name "constructor")
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.method)
)

(
  (comment)* @doc
  .
  [
    (class
      name: (_) @name)
    (class_declaration
      name: (_) @name)
  ] @definition.class
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.class)
)

(
  (comment)* @doc
  .
  [
    (function_expression
      name: (identifier) @name)
    (function_declaration
      name: (identifier) @name)
    (generator_function
      name: (identifier) @name)
    (generator_function_declaration
      name: (identifier) @name)
  ] @definition.function
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.function)
)

(
  (comment)* @doc
  .
  (lexical_declaration
    (variable_declarator
      name: (identifier) @name
      value: [(arrow_function) (function_expression)]) @definition.function)
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.function)
)

(
  (comment)* @doc
  .
  (variable_declaration
    (variable_declarator
      name: (identifier) @name
      value: [(arrow_function) (function_expression)]) @definition.function)
  (#strip! @doc "^[\\s\\*/]+|^[\\s\\*/]$")
  (#select-adjacent! @doc @definition.function)
)

(assignment_expression
  left: [
    (identifier) @name
    (member_expression
      property: (property_identifier) @name)
  ]
  right: [(arrow_function) (function_expression)]
) @definition.function

(pair
  key: (property_identifier) @name
  value: [(arrow_function) (function_expression)]) @definition.function

(
  (call_expression
    function: (identifier) @name) @reference.call
  (#not-match? @name "^(require)$")
)

(call_expression
  function: (member_expression
    property: (property_identifier) @name)
  arguments: (_) @reference.call)

(new_expression
  constructor: (_) @name) @reference.class

(export_statement value: (assignment_expression left: (identifier) @name right: ([
 (number)
 (string)
 (identifier)
 (undefined)
 (null)
 (new_expression)
 (binary_expression)
 (call_expression)
]))) @definition.constant

; --- tree-sitter-typescript 0.23.2 queries/tags.scm ---
(function_signature
  name: (identifier) @name) @definition.function

(method_signature
  name: (property_identifier) @name) @definition.method

(abstract_method_signature
  name: (property_identifier) @name) @definition.method

(abstract_class_declaration
  name: (type_identifier) @name) @definition.class

(module
  name: (identifier) @name) @definition.module

(interface_declaration
  name: (type_identifier) @name) @definition.interface

(type_annotation
  (type_identifier) @name) @reference.type

(new_expression
  constructor: (identifier) @name) @reference.class

; --- OXIDE additions: neither upstream file defines these constructs at all
; (type_annotation above only captures a *usage* of a type name, never the
; alias/enum declaration itself). Kept to two lines, same capture grammar as
; everything above, so tree-sitter-tags' own query validation accepts them
; without any extra plumbing.
(type_alias_declaration
  name: (type_identifier) @name) @definition.type_alias

(enum_declaration
  name: (identifier) @name) @definition.enum

; JS's own method_definition pattern above excludes the constructor
; (`#not-eq? @name "constructor"`) by ctags convention — a real gap for
; OXIDE, where a constructor's parameter list is often exactly what a coding
; agent needs (dependency injection, required fields). Restore it narrowly.
(method_definition
  name: (property_identifier) @name
  (#eq? @name "constructor")) @definition.method

; JS's own @definition.constant pattern above only matches `export_statement
; value: (assignment_expression ...)` — the rare bare `export X = ...` form.
; The common `export const X = ...` form is structurally a lexical_declaration
; under export_statement's `declaration` field, a different shape entirely,
; so it's invisible to that pattern regardless of the value's type. Measured
; impact: fixtures/benchmark.json's `ts-default-policy-const` task (recall@5
; 1.000 with the handwritten extractor) scored 0.000 without this — the
; symbol simply wasn't indexed. Value types mirror JS's own allowlist, minus
; arrow_function/function_expression (already @definition.function above;
; including them here would double-tag the same declarator two ways).
(export_statement
  declaration: (lexical_declaration
    (variable_declarator
      name: (identifier) @name
      value: [
        (number)
        (string)
        (identifier)
        (undefined)
        (null)
        (new_expression)
        (binary_expression)
        (call_expression)
        (object)
        (array)
        (template_string)
      ]))) @definition.constant
