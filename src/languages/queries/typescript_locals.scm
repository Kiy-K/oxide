; tree-sitter-typescript 0.23.2 queries/locals.scm, verbatim (MIT license).
; Low value for OXIDE today (only parameter-scoping, no variable/function
; local-reference resolution) but included since TagsConfiguration::new
; concatenates locals_query ahead of tags_query and upstream expects the pair.

(required_parameter (identifier) @local.definition)
(optional_parameter (identifier) @local.definition)
