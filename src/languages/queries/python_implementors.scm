; Direct Tree-sitter implementors query. One declarative pattern handles any
; number of base classes — the query engine yields one match per base
; identifier directly under `superclasses`, so N-ary inheritance needs no
; enumerated pattern variants (unlike structural.rs's ast-grep patterns,
; which hand-list first/middle/last-position base-list shapes per language).
; A base that's a qualified attribute (`abc.ABC`) or a keyword argument
; (`metaclass=ABCMeta`) doesn't match `(identifier)` here, matching
; structural.rs's ast-grep patterns, which also only match a bare identifier
; in that position.
(class_definition
  name: (identifier) @name
  superclasses: (argument_list
    (identifier) @base)) @class
