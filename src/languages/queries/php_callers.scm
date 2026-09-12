; Call sites: a free call, an instance call (`$obj->run()`), a static call
; (`Store::create()`), and construction (`new Store()`).
;
; `new self()` / `new static()` / `new parent()` name no callee a reader
; could look up — they are the *enclosing* class under another spelling —
; so they are excluded rather than handing every PHP class's constructor to
; a symbol named `self`.
;
; A variable call (`$fn()`, `new $cls`) is skipped: the callee is a runtime
; value, the same reason Ruby's bare identifiers and Java's method
; references are skipped.

(function_call_expression
  function: [
    (name) @name
    (qualified_name (name) @name)
  ]) @call

(member_call_expression
  name: (name) @name) @call

(scoped_call_expression
  name: (name) @name) @call

(object_creation_expression
  [
    (name) @name
    (qualified_name (name) @name)
  ]
  (#not-any-of? @name "self" "static" "parent")) @call
