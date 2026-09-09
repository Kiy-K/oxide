; Same call-site shape as the @reference.call captures in rust_tags.scm:
; a bare call, a method call through a field expression, and a macro
; invocation. Macros are included because in Rust they are frequently how a
; symbol is actually used (`assert_eq!`, `println!`, a derive helper) and
; `calls` is a bare-name tier where `foo!` and `foo` are the same token
; anyway — `last_segment` strips the `!` along with any path qualification.
(call_expression
  function: [
    (identifier) @name
    (scoped_identifier
      name: (identifier) @name)
    (field_expression
      field: (field_identifier) @name)
  ]) @call

; `std::println!(..)` and `crate::metrics::emit!(..)` put a
; `scoped_identifier` in the `macro` field, so a bare-identifier pattern
; misses them entirely — the qualified ordinary-call case above already had
; its own alternative for the same reason.
(macro_invocation
  macro: [
    (identifier) @name
    (scoped_identifier
      name: (identifier) @name)
  ]) @call
