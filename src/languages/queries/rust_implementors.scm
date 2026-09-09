; Rust's inheritance analogue is `impl Trait for Type`, so the base is the
; trait and the implementor is the type. `@class` is the impl block, whose
; line is what `structural_relations` attributes by; the impl block's own
; symbol is usually deduped away in favour of the `struct Foo` that declared
; the name, which is why attribution falls back to a unique same-named
; Class/Interface in the file (see `compute_file_relations`).
;
; A supertrait bound (`trait A: B`) is the other real base-like relation and
; is captured too — a trait with a supertrait is as much an implementor of
; it as a struct is.
(impl_item
  trait: [
    (type_identifier) @base
    (generic_type type: (type_identifier) @base)
    (scoped_type_identifier name: (type_identifier) @base)
  ]
  type: [
    (type_identifier) @name
    (generic_type type: (type_identifier) @name)
  ]) @class

(trait_item
  name: (type_identifier) @name
  bounds: (trait_bounds
    [
      (type_identifier) @base
      (generic_type type: (type_identifier) @base)
    ])) @class
