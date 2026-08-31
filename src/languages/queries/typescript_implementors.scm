; Direct Tree-sitter implementors query, shared by TypeScript and TSX. One
; declarative pattern per clause type handles any number of extended/
; implemented types — the query engine yields one match per type node
; directly under `implements_clause` (or `extends_clause`'s value), so an
; `implements A, B` list or an `extends Base implements Iface` combination
; needs no enumerated positional pattern variants (unlike structural.rs's
; ast-grep patterns, which hand-list first/middle/last-position variants).
; A qualified extends value (`ns.Base`) doesn't match `(identifier)` here,
; matching structural.rs's ast-grep patterns, which also only match a bare
; identifier in that position.
;
; Three top-level patterns cover the grammar's three class-heritage shapes:
; `class_declaration` (name required), `abstract_class_declaration` (name
; required, a distinct node kind from plain `class_declaration` — confirmed
; via tree-sitter-typescript's node-types.json, and already relied on
; separately by typescript_tags.scm's @definition.class capture), and the
; anonymous class-expression form `class` (name optional — `?` below, since
; `const Worker = class implements Runnable {}` has no name node at all;
; requiring @name there would silently drop every anonymous implementor).
(class_declaration
  name: (type_identifier) @name
  (class_heritage
    [
      (extends_clause value: (identifier) @base)
      (implements_clause (type_identifier) @base)
    ])) @class

(abstract_class_declaration
  name: (type_identifier) @name
  (class_heritage
    [
      (extends_clause value: (identifier) @base)
      (implements_clause (type_identifier) @base)
    ])) @class

(class
  name: (type_identifier)? @name
  (class_heritage
    [
      (extends_clause value: (identifier) @base)
      (implements_clause (type_identifier) @base)
    ])) @class
