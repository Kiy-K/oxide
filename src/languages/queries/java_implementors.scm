; extends / implements, one match per named base.
;
; Upstream tags reports `type_list` once per name but at the *clause's* byte
; range, so `implements Backend, Cloneable` gives both names the same
; position and nothing can tell them apart. Capturing the individual type
; nodes here is what makes each base its own relation — the same shape
; TypeScript's implements clause already needed.
;
; `@class` is the declaration node, whose start line attributes the clause
; to its owning symbol. Java's declaration node includes its annotations, so
; that line matches the symbol's own `start_line` exactly (unlike Python and
; TypeScript, where `decorator_extended_start` widens the symbol past the
; node the query reports).

(class_declaration
  name: (identifier) @name
  superclass: (superclass (_) @base)) @class

(class_declaration
  name: (identifier) @name
  interfaces: (super_interfaces (type_list (_) @base))) @class

(interface_declaration
  name: (identifier) @name
  (extends_interfaces (type_list (_) @base))) @class

(enum_declaration
  name: (identifier) @name
  interfaces: (super_interfaces (type_list (_) @base))) @class

(record_declaration
  name: (identifier) @name
  interfaces: (super_interfaces (type_list (_) @base))) @class
