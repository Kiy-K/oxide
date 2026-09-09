; Mirrors the @reference.call shape in go_tags.scm: bare calls and calls
; through a selector both count as a call site. The parenthesized variants
; upstream enumerates are dropped — a parenthesized call target is a
; conversion (`(*T)(p)`), not a call OXIDE wants attributed.
(call_expression
  function: [
    (identifier) @name
    (selector_expression
      field: (field_identifier) @name)
  ]) @call
