; Go has no `implements` keyword — interface satisfaction is structural and
; a semantic question this project deliberately does not answer. Embedding
; is the one syntactic inheritance-shaped relation available, so that is
; what `bases` means for Go: an embedded struct field or an embedded
; interface element.
;
; `!name` is what distinguishes an embedded field (`Base`) from a named one
; (`base Base`); the same negation upstream's own tags.scm uses for `!trait`
; in Rust.
(type_spec
  name: (type_identifier) @name
  type: (struct_type
    (field_declaration_list
      (field_declaration
        !name
        type: [
          (type_identifier) @base
          (qualified_type name: (type_identifier) @base)
          (pointer_type (type_identifier) @base)
          (pointer_type (qualified_type name: (type_identifier) @base))
        ])))) @class

(type_spec
  name: (type_identifier) @name
  type: (interface_type
    (type_elem
      [
        (type_identifier) @base
        (qualified_type name: (type_identifier) @base)
      ]))) @class
