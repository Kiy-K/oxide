; Direct Tree-sitter callers query, shared by TypeScript and TSX (same
; grammar family/node kinds for these two patterns). Mirrors the
; @reference.call shape in typescript_tags.scm: bare calls and
; member/method calls both count as a call site.
(call_expression
  function: (identifier) @name) @call

(call_expression
  function: (member_expression
    property: (property_identifier) @name)) @call
