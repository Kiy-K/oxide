; Upstream tree-sitter-rust 0.24.2 queries/tags.scm (MIT license,
; https://github.com/tree-sitter/tree-sitter-rust) with two OXIDE changes,
; both marked inline below:
;
;   1. `type_item` is retagged @definition.type_alias. Upstream lumps it in
;      with struct/enum/union as @definition.class; OXIDE has a TypeAlias
;      kind and a `type Foo = Bar;` labelled "class" is just wrong.
;   2. Two @definition.class patterns for `impl_item` are appended. Upstream
;      only ever captures an impl block as @reference.implementation, so the
;      containment stack in tags.rs has nothing to nest an impl's methods
;      under and every `fn new` in a file collides on the bare name `new`
;      (parser.rs's qualified-name dedup then silently drops all but one).
;      Capturing the impl by its *type* name makes methods come out as
;      `Foo.new`. The impl block's own symbol is normally dropped by that
;      same dedup — the `struct Foo` earlier in the file already owns the
;      name — which is the intended outcome: the methods keep
;      `parent: Some("Foo")`, pointing at the struct.

; --- OXIDE-owned: impl blocks as containers (see header) ---
; Placed ahead of upstream's patterns deliberately: upstream's
; `(impl_item type: (type_identifier) @name !trait) @reference.implementation`
; matches the same node and name for an inherent `impl Store`, and whichever
; pattern the tags engine resolves last wins — with these below it, every
; inherent impl came back as a reference, so its methods never nested.

(impl_item
    type: [
        (type_identifier) @name
        (generic_type
            type: (type_identifier) @name)
        (scoped_type_identifier
            name: (type_identifier) @name)
        (generic_type
            type: (scoped_type_identifier
                name: (type_identifier) @name))
    ]) @definition.class

; ADT definitions

(struct_item
    name: (type_identifier) @name) @definition.class

; OXIDE: retagged from @definition.class — OXIDE has an Enum kind.
(enum_item
    name: (type_identifier) @name) @definition.enum

(union_item
    name: (type_identifier) @name) @definition.class

; type aliases

; OXIDE: retagged from @definition.class (see header)
(type_item
    name: (type_identifier) @name) @definition.type_alias

; method definitions
; OXIDE: anchored to impl/trait bodies. Upstream matches any
; `declaration_list`, and a `mod` body is one too, so every free function
; inside `mod net { .. }` came out labelled "method".

(impl_item
    body: (declaration_list
        (function_item
            name: (identifier) @name) @definition.method))

(trait_item
    body: (declaration_list
        (function_item
            name: (identifier) @name) @definition.method))

(trait_item
    body: (declaration_list
        (function_signature_item
            name: (identifier) @name) @definition.method))

; function definitions

(function_item
    name: (identifier) @name) @definition.function

; trait definitions
(trait_item
    name: (type_identifier) @name) @definition.interface

; module definitions
(mod_item
    name: (identifier) @name) @definition.module

; macro definitions

(macro_definition
    name: (identifier) @name) @definition.macro

; references

(call_expression
    function: (identifier) @name) @reference.call

(call_expression
    function: (field_expression
        field: (field_identifier) @name)) @reference.call

(macro_invocation
    macro: (identifier) @name) @reference.call

; implementations

(impl_item
    trait: (type_identifier) @name) @reference.implementation

(impl_item
    type: (type_identifier) @name
    !trait) @reference.implementation

