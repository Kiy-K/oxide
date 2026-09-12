; Upstream tree-sitter-php 0.24.2 queries/tags.scm (MIT license,
; https://github.com/tree-sitter/tree-sitter-php) with three changes.
;
; Dropped: every `@reference.*` pattern (OXIDE discards non-definition tags,
; and the call/creation shapes it does want live in `php_callers.scm` where
; they can be filtered), and `@definition.field` for properties — OXIDE has
; no variable kind, so `map_kind` would discard those tags anyway. That is
; the same call Java's fields got.
;
; Retagged: `method_declaration` from upstream's `@definition.function` to
; `@definition.method`. PHP is not Python, so `tags.rs` does not reclassify
; a function nested in a class, and leaving it would label every PHP method
; a free function.
;
; Added: enums (PHP 8.1) and their cases, plus `const` — a class constant
; and a file-level `const` are both `const_declaration`, so one pattern
; covers both and containment decides which is which.

(namespace_definition
  name: (namespace_name) @name) @definition.module

(interface_declaration
  name: (name) @name) @definition.interface

(trait_declaration
  name: (name) @name) @definition.interface

(class_declaration
  name: (name) @name) @definition.class

(function_definition
  name: (name) @name) @definition.function

(method_declaration
  name: (name) @name) @definition.method

; --- OXIDE-owned ---

(enum_declaration
  name: (name) @name) @definition.enum

(enum_case
  name: (name) @name) @definition.constant

(const_declaration
  (const_element
    (name) @name)) @definition.constant
