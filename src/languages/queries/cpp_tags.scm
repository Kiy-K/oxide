; C++ definitions. `function_declarator` stays fixed under return-type
; wrappers; tags.rs restores definitions' full body spans and identity.

(namespace_definition
  name: (namespace_identifier) @name) @definition.module

(class_specifier
  name: (type_identifier) @name
  body: (_)) @definition.class

(struct_specifier
  name: (type_identifier) @name
  body: (_)) @definition.class

(enum_specifier
  name: (type_identifier) @name
  body: (_)) @definition.enum

(operator_cast
  type: (_) @name) @definition.function

(function_declarator
  declarator: [
    (identifier) @name
    (field_identifier) @name
    (operator_name) @name
    (destructor_name) @name
  ]) @definition.function

(function_declarator
  declarator: (qualified_identifier
    (_)*
    [
      (identifier) @name
      (field_identifier) @name
      (operator_name) @name
      (destructor_name) @name
    ])) @definition.function
