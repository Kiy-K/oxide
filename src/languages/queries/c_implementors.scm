; C has no inheritance. It does have one relation with the same shape and
; the same syntactic evidence Go's embedding has: a struct whose **first**
; member is another named struct, which is the idiom that makes a pointer to
; the outer type castable to the inner one.
;
; First member only, via the `.` anchor. Any-position would report ordinary
; composition — a struct that merely *holds* another — as inheritance, which
; is a different and far more common thing.
;
; `typedef struct { struct base base; ... } derived_t;` is not matched: the
; struct is anonymous, so there is no `name:` to key the relation by. That
; is the same reason an anonymous class expression is skipped in TypeScript.

(struct_specifier
  name: (type_identifier) @name
  body: (field_declaration_list
    .
    (field_declaration
      type: (struct_specifier
        name: (type_identifier) @base)))) @class
