; `extends`, `implements`, and trait `use`.
;
; A trait is PHP's mixin and `use Cacheable;` inside a class body is how it
; is applied — the same relation Ruby's `include` carries, and the same
; reason it lands in `bases` rather than in `calls`. Matched only as a
; direct child of the declaration's own body, so a nested declaration's
; traits are never attributed to its enclosing one.
;
; `base_clause` and `class_interface_clause` are anonymous children, not
; fields, and each yields one match per named base — `implements A, B` is
; two relations, which is what `all_bases_in_file` needs.
;
; A leading-backslash or namespaced base (`\JsonSerializable`,
; `App\Contracts\Backend`) is reduced to its last segment by
; `tree_sitter_structural::last_segment`, which is why that function splits
; on `\` as well as `.` and `::`.

(class_declaration
  name: (name) @name
  (base_clause [
    (name) @base
    (qualified_name) @base
  ])) @class

(class_declaration
  name: (name) @name
  (class_interface_clause [
    (name) @base
    (qualified_name) @base
  ])) @class

(interface_declaration
  name: (name) @name
  (base_clause [
    (name) @base
    (qualified_name) @base
  ])) @class

(enum_declaration
  name: (name) @name
  (class_interface_clause [
    (name) @base
    (qualified_name) @base
  ])) @class

(class_declaration
  name: (name) @name
  body: (declaration_list
    (use_declaration [
      (name) @base
      (qualified_name) @base
    ]))) @class

(trait_declaration
  name: (name) @name
  body: (declaration_list
    (use_declaration [
      (name) @base
      (qualified_name) @base
    ]))) @class

(enum_declaration
  name: (name) @name
  body: (enum_declaration_list
    (use_declaration [
      (name) @base
      (qualified_name) @base
    ]))) @class
