; Direct Tree-sitter callers query (tree_sitter_structural.rs's experimental
; alternative to structural.rs's ast-grep patterns). Same call-site shape as
; the @reference.call capture in python_tags.scm (upstream tags.scm) — bare
; calls and attribute/method calls both count as a call site.
(call
  function: [
    (identifier) @name
    (attribute
      attribute: (identifier) @name)
  ]) @call
