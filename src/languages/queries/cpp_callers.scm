(call_expression
  function: (identifier) @name) @call

(call_expression
  function: (field_expression
    field: (field_identifier) @name)) @call

(call_expression
  function: (qualified_identifier
    name: (identifier) @name)) @call
