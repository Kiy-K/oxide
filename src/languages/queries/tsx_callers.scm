; TSX-only addition, concatenated after typescript_callers.scm (these node
; kinds exist in the TSX grammar and not in the TypeScript one, so compiling
; them against LANGUAGE_TYPESCRIPT would fail outright).
;
; Rendering `<Button />` is treated as a call of `Button`: `callers_of` is
; how `context.rs` answers "what uses this symbol", and for a component the
; honest answer is the components that render it. Without this, JSX usage
; was visible only to `extract_references`'s token scan — the weakest
; confidence tier — while the AST-precise tier saw nothing at all.
; A qualified element name (`<ns.Button />`) reduces to its last segment
; like every other name in this tier.
(jsx_opening_element
  name: (_) @name) @call

(jsx_self_closing_element
  name: (_) @name) @call
