; Call sites. Ruby's `(call)` node covers both a receiverless `helper(1)`
; and a method call `obj.helper`, so one pattern gets both.
;
; A bare `helper` with neither parentheses nor receiver is an `(identifier)`
; and is syntactically indistinguishable from a local variable read, so it
; is deliberately not matched — the same conservatism Java's query applies
; to method references.
;
; `Foo.new` is Ruby's construction syntax, and the interesting relation is
; "who constructs Foo", not "who calls new". The second pattern captures the
; receiver for exactly that shape; the first excludes `new` so a single
; `Foo.new` yields one relation, not two.
; `include`/`extend`/`prepend` and `require`/`require_relative`/`load` are
; excluded because OXIDE already records exactly what they say, as a base
; (`ruby_implementors.scm`) and as an import (`tags.rs::collect_meta`)
; respectively. Emitting them here too would restate the same fact as a
; third relation and hand every caller in the repo to any symbol unlucky
; enough to be named `include`.
(call
  method: (identifier) @name
  (#not-any-of? @name
    "new"
    "include" "extend" "prepend"
    "require" "require_relative" "load")) @call

(call
  receiver: [
    (constant) @name
    (scope_resolution) @name
  ]
  method: (identifier) @_ctor
  (#eq? @_ctor "new")) @call
