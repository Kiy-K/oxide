; Upstream tree-sitter-python 0.25.0 queries/tags.scm, verbatim (MIT license,
; https://github.com/tree-sitter/tree-sitter-python). Flat: no parent tracking,
; no method-vs-function split (both @definition.function; OXIDE's normalizer
; reclassifies a function as Method when its nearest enclosing definition is a
; class, mirroring the removed hand-written extractor's frame-stack logic).

(module (expression_statement (assignment left: (identifier) @name) @definition.constant))

(class_definition
  name: (identifier) @name) @definition.class

(function_definition
  name: (identifier) @name) @definition.function

(call
  function: [
      (identifier) @name
      (attribute
        attribute: (identifier) @name)
  ]) @reference.call
