; Direct Tree-sitter implementors query. One declarative pattern handles any
; number of base classes — the query engine yields one match per base
; identifier directly under `superclasses`, so N-ary inheritance needs no
; enumerated pattern variants (unlike structural.rs's ast-grep patterns,
; which hand-list first/middle/last-position base-list shapes per language).
; A qualified base (`abc.ABC`) is an `attribute` node, matched here and
; reduced to its last segment (`ABC`) by the caller — `calls`/`bases` are a
; bare-name tier, so a qualified base that resolved to nothing at all was a
; pure loss, not a deliberate precision choice. A keyword argument
; (`metaclass=ABCMeta`) still matches neither alternative and stays out.
(class_definition
  name: (identifier) @name
  superclasses: (argument_list
    [
      (identifier) @base
      (attribute) @base
    ])) @class
