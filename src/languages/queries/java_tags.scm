; OXIDE-owned Java tags query.
;
; Upstream tree-sitter-java's tags.scm tags only class, interface and method
; declarations (plus references OXIDE ignores) — the thinnest of any grammar
; used here, per docs/java-feasibility/README.md. Constructors, enums,
; records and annotation types are appended below; without them a builder's
; constructors and every `enum`/`record` in a modern codebase are invisible.
;
; Annotations need no equivalent of `tags.rs::decorator_extended_start`:
; Java's grammar puts `modifiers` (which holds annotations) *inside* the
; declaration node, so `@Service`/`@Override` are already within the tag's
; byte range and therefore within the symbol's span, hash and signature.

(class_declaration
  name: (identifier) @name) @definition.class

(interface_declaration
  name: (identifier) @name) @definition.interface

(annotation_type_declaration
  name: (identifier) @name) @definition.interface

(enum_declaration
  name: (identifier) @name) @definition.enum

(record_declaration
  name: (identifier) @name) @definition.class

(method_declaration
  name: (identifier) @name) @definition.method

(constructor_declaration
  name: (identifier) @name) @definition.method

(compact_constructor_declaration
  name: (identifier) @name) @definition.method
