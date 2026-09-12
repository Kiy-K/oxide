; One match per C++ base clause. C++ supports several access forms, but all
; of them contain a type node; this remains a bare-name relation like every
; other language's inheritance edge.
(class_specifier
  name: (type_identifier) @name
  (base_class_clause
    [
      (type_identifier) @base
      (qualified_identifier) @base
      (template_type) @base
    ])) @class

(struct_specifier
  name: (type_identifier) @name
  (base_class_clause
    [
      (type_identifier) @base
      (qualified_identifier) @base
      (template_type) @base
    ])) @class
