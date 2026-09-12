; Call sites: `@name` is the callee, `@call` the enclosing expression whose
; start line attributes the call to a symbol (structural_relations.rs).
; Conservative by design — a `method_invocation` and a `new X(...)` are the
; two shapes with an unambiguous callee name. Method references (`Foo::bar`)
; are deliberately absent: the grammar gives two identifiers with no field
; distinguishing receiver from member, so the callee is a guess.
;
; The wildcard `type: (_)` covers `new X()`, `new pkg.X()` and `new X<T>()`
; in one pattern; `tree_sitter_structural::last_segment` brings all three
; back onto the bare-name tier every other language's relations live on.

(method_invocation
  name: (identifier) @name) @call

(object_creation_expression
  type: (_) @name) @call
