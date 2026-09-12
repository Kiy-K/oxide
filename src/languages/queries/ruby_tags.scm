; Upstream tree-sitter-ruby 0.23.1 queries/tags.scm (MIT license,
; https://github.com/tree-sitter/tree-sitter-ruby) minus its two
; `@reference.call` patterns, plus OXIDE-owned additions at the bottom.
;
; The reference patterns are dropped rather than kept-and-filtered like
; Python's and Go's: Ruby's second one is `[(identifier) (constant)] @name
; @reference.call (#is-not? local)`, which matches *every identifier and
; constant in the file* and leans on a locals query to thin the result.
; OXIDE passes `locals_query: ""` (see `RUBY_PROFILE`) and discards every
; non-definition tag anyway, so keeping it would buy a whole-file match per
; token and nothing else.
;
; `singleton_method` (`def self.create`) is tagged as a method like any
; other, and the bare name it reports would collide with a same-named
; instance method under `parser.rs`'s first-wins dedup — see
; `tags.rs::FileMeta::ruby_singletons` for the prefix that keeps them apart.

(
  (comment)* @doc
  .
  [
    (method
      name: (_) @name) @definition.method
    (singleton_method
      name: (_) @name) @definition.method
  ]
  (#strip! @doc "^#\\s*")
  (#select-adjacent! @doc @definition.method)
)

(alias
  name: (_) @name) @definition.method

(setter
  (identifier) @ignore)

(
  (comment)* @doc
  .
  [
    (class
      name: [
        (constant) @name
        (scope_resolution
          name: (_) @name)
      ]) @definition.class
    (singleton_class
      value: [
        (constant) @name
        (scope_resolution
          name: (_) @name)
      ]) @definition.class
  ]
  (#strip! @doc "^#\\s*")
  (#select-adjacent! @doc @definition.class)
)

(
  (module
    name: [
      (constant) @name
      (scope_resolution
        name: (_) @name)
    ]) @definition.module
)

; --- OXIDE-owned ---
; Constant assignment. Upstream tags no constants at all, so `VERSION`,
; `DEFAULTS = {...}` and every `Struct.new`-assigned class were invisible —
; in Ruby a top-level constant is frequently the configuration a reader is
; hunting for, and inside a class body it is the class's public surface.
; Only `(constant)` on the left, never `(identifier)`: a lowercase
; assignment is a local variable, not a definition.
(assignment
  left: (constant) @name) @definition.constant
