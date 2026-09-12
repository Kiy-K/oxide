; Inheritance and mixins.
;
; `class Foo < Bar` is the only true inheritance Ruby has, but `include` /
; `extend` / `prepend` are how Ruby codebases actually compose behavior, and
; they map cleanly to syntax: a receiverless call to one of those three
; names, directly in a class or module body, with a constant argument. That
; is the same relation `implements` carries elsewhere, so it lands in
; `bases`.
;
; Matched only as a *direct* child of the body: an `include` nested inside a
; conditional or a block is conditional behavior, not a declared base, and
; walking deeper would also start attributing a nested class's mixins to its
; enclosing one.
(class
  name: [(constant) (scope_resolution)] @name
  superclass: (superclass [
    (constant) @base
    (scope_resolution) @base
  ])) @class

(class
  name: [(constant) (scope_resolution)] @name
  body: (body_statement
    (call
      method: (identifier) @_mixin
      arguments: (argument_list [
        (constant) @base
        (scope_resolution) @base
      ])))
  (#any-of? @_mixin "include" "extend" "prepend")) @class

(module
  name: [(constant) (scope_resolution)] @name
  body: (body_statement
    (call
      method: (identifier) @_mixin
      arguments: (argument_list [
        (constant) @base
        (scope_resolution) @base
      ])))
  (#any-of? @_mixin "include" "extend" "prepend")) @class
