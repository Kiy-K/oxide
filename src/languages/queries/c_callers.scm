; Call sites. The second pattern is not optional in C: dispatching through a
; function pointer held in a struct field (`s->ops->read(buf)`) is how C
; codebases do virtual dispatch, and a query that only matched bare
; identifiers would miss every one of them.
;
; A call through a plain pointer variable (`cb(x)` where `cb` is a
; parameter) is indistinguishable from a direct call and is reported as a
; call of `cb` — the bare-name tier makes no claim to have resolved it.

(call_expression
  function: (identifier) @name) @call

(call_expression
  function: (field_expression
    field: (field_identifier) @name)) @call
