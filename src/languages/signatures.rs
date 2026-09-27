//! Overload-bearing languages' parameter-type normalization: the text a
//! Java or C++ method/constructor's `qualified_name` carries so overloads
//! stay distinct symbols (`Store.get(String,String)`). Pure functions of a
//! parameter-list node and the source; `tags.rs::collect_meta` calls them
//! and owns everything else about extraction.
//!
//! Normalization here is persisted identity: a changed output renames the
//! symbol, which moves its id and re-embeds it (AGENTS.md, "Symbol ids").

use tree_sitter::Node;

/// One Java parameter type, normalized the way the JVM's own overload rules
/// are: generics erased (`List<String>` and `List<Integer>` cannot coexist
/// as overloads, so keeping the argument would only make two names for what
/// Java treats as one), package qualifiers dropped to the last segment
/// (`java.util.Set` and an imported `Set` are the same type written two
/// ways), array dimensions kept (`byte[]` really is a distinct overload).
/// Parameter *names*, `final`, and parameter annotations never appear: they
/// are separate grammar children, and renaming a parameter — or annotating
/// it — must not change a symbol's persisted identity.
fn java_type_name(text: &str) -> String {
    // Strip every balanced `<...>` region first, so dimensions belonging to
    // an array-of-generic (`List<String>[]`) survive the erasure.
    let mut base = String::new();
    let mut depth = 0usize;
    for ch in text.chars() {
        match ch {
            '<' => depth += 1,
            '>' => depth = depth.saturating_sub(1),
            _ if depth == 0 => base.push(ch),
            _ => {}
        }
    }
    let dims = base.matches('[').count();
    let head = base.split('[').next().unwrap_or(&base);
    let short = head.rsplit('.').next().unwrap_or(head).trim();
    // A *type* annotation (JSR-308: `java.util.@NonNull List`) lives inside
    // the type node, unlike a parameter annotation, which the grammar puts
    // in a sibling `modifiers`. Without dropping it here, adding or removing
    // `@NonNull` would rewrite the method's qualified name — and therefore
    // its persisted id — for a method that did not change, orphaning its
    // embedding and re-embedding it on the next index.
    let mut out: String = short
        .split_whitespace()
        .filter(|t| !t.starts_with('@'))
        .collect();
    for _ in 0..dims {
        out.push_str("[]");
    }
    out
}

/// Comma-joined normalized parameter types of a `formal_parameters` node, in
/// declaration order — the discriminator that keeps Java overloads apart.
///
/// Java overloading is idiomatic and pervasive, so without this every
/// `Store.get(...)` in a file collapses to the qualified name `Store.get`
/// and `parser.rs::parse_file_with`'s first-wins dedup silently drops the
/// rest (docs/java-feasibility/README.md's blocker). Putting the signature
/// in the *name* rather than changing `Symbol::id`'s composition is what
/// makes this safe: the id formula is still `FNV1a(file + \0 +
/// qualified_name)`, so every Python/TypeScript/TSX/Rust/Go id in every
/// existing index is bit-identical and nothing re-embeds
/// (`tests/language_conformance.rs`'s committed goldens are the proof).
///
/// A `spread_parameter` (`int... flags`) has no `type` field — its type is
/// its first named child — and erases to an array, exactly as the JVM does,
/// so `f(int...)` and `f(int[])` normalize alike and cannot both exist.
pub(super) fn java_signature(params: Node<'_>, src: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut cur = params.walk();
    for child in params.named_children(&mut cur) {
        match child.kind() {
            "formal_parameter" => {
                if let Some(t) = child.child_by_field_name("type") {
                    if let Ok(text) = t.utf8_text(src.as_bytes()) {
                        out.push(java_type_name(text));
                    }
                }
            }
            "spread_parameter" => {
                // Unlike `formal_parameter`, a spread parameter exposes no
                // `type` field — its type is a positional child. It is not
                // reliably the *first* one, though: `final int... flags` and
                // `@NonNull String... xs` put a `modifiers` node ahead of it,
                // which produced `f(final[])` and `f([])` and, worse, moved
                // the method's persisted id whenever somebody added `final`
                // or an annotation (found by review).
                if let Some(t) = child
                    .named_children(&mut child.walk())
                    .find(|c| c.kind() != "modifiers")
                {
                    if let Ok(text) = t.utf8_text(src.as_bytes()) {
                        out.push(format!("{}[]", java_type_name(text)));
                    }
                }
            }
            // `receiver_parameter` (`Outer Outer.this`) is not an argument.
            _ => {}
        }
    }
    out.join(",")
}

/// C++ overload identity keeps template arguments (unlike Java's JVM-shaped
/// erasure), normalizes punctuation whitespace, and ignores parameter names
/// and defaults. `const`/`volatile` qualifiers on the type and `&`/`&&`
/// declarators remain because they can distinguish overloads.
pub(super) fn cpp_type_name(text: &str) -> String {
    let mut out = String::new();
    let mut pending_space = false;
    for ch in text.trim().chars() {
        if ch.is_whitespace() {
            pending_space = !out.is_empty();
            continue;
        }
        let punctuation = matches!(ch, ':' | '<' | '>' | ',' | '*' | '&' | '[' | ']');
        if pending_space && !punctuation && !out.ends_with([':', '<', ',', '*', '&', '[']) {
            out.push(' ');
        }
        if punctuation && out.ends_with(' ') {
            out.pop();
        }
        out.push(ch);
        pending_space = false;
    }
    out
}

fn cpp_declarator_suffix(node: Node<'_>, src: &str) -> String {
    // Iterative, because the declarator chain is as long as the source makes
    // it: descend to the innermost declarator, then build outward. Each
    // pointer/reference level prepends its `*`/`&`, each array level appends
    // its dimensions, parentheses contribute nothing — the same string the
    // recursive "outer part + inner suffix" definition produced. A level
    // with no inner declarator ends the chain and contributes nothing.
    enum Level<'s> {
        Prefix(String),
        Dims(&'s str),
    }
    let mut levels = Vec::new();
    let mut node = node;
    while let Some(inner) = match node.kind() {
        "reference_declarator"
        | "pointer_declarator"
        | "array_declarator"
        | "parenthesized_declarator" => node.named_child(0),
        _ => None,
    } {
        match node.kind() {
            "reference_declarator" | "pointer_declarator" => levels.push(Level::Prefix(
                src[node.start_byte()..inner.start_byte()]
                    .chars()
                    .filter(|c| matches!(c, '*' | '&'))
                    .collect(),
            )),
            "array_declarator" => levels.push(Level::Dims(&src[inner.end_byte()..node.end_byte()])),
            _ => {}
        }
        node = inner;
    }
    let mut suffix = String::new();
    for level in levels.into_iter().rev() {
        match level {
            Level::Prefix(p) => suffix.insert_str(0, &p),
            Level::Dims(d) => suffix.push_str(&d.replace(char::is_whitespace, "")),
        }
    }
    suffix
}

fn cpp_parameter_type(param: Node<'_>, src: &str) -> Option<String> {
    let ty = param.child_by_field_name("type")?;
    let mut prefix = param
        .named_children(&mut param.walk())
        .filter(|child| child.kind() == "type_qualifier")
        .filter_map(|child| child.utf8_text(src.as_bytes()).ok())
        .collect::<Vec<_>>();
    prefix.push(ty.utf8_text(src.as_bytes()).ok()?);
    let mut out = cpp_type_name(&prefix.join(" "));
    if let Some(declarator) = param.child_by_field_name("declarator") {
        out.push_str(&cpp_declarator_suffix(declarator, src));
    }
    Some(out)
}

pub(super) fn cpp_signature(params: Node<'_>, src: &str) -> String {
    params
        .named_children(&mut params.walk())
        .filter(|child| {
            matches!(
                child.kind(),
                "parameter_declaration" | "optional_parameter_declaration"
            )
        })
        .filter_map(|child| cpp_parameter_type(child, src))
        .collect::<Vec<_>>()
        .join(",")
}

pub(super) fn cpp_method_qualifier(declarator: Node<'_>, src: &str) -> String {
    let Some(params) = declarator.child_by_field_name("parameters") else {
        return String::new();
    };
    let mut is_const = false;
    let mut is_volatile = false;
    let mut reference = None;
    for child in declarator.named_children(&mut declarator.walk()) {
        if child.start_byte() < params.end_byte() {
            continue;
        }
        let Ok(text) = child.utf8_text(src.as_bytes()) else {
            continue;
        };
        match (child.kind(), text.trim()) {
            ("type_qualifier", "const") => is_const = true,
            ("type_qualifier", "volatile") => is_volatile = true,
            ("ref_qualifier", "&" | "&&") => reference = Some(text.trim()),
            _ => {}
        }
    }
    format!(
        "{}{}{}",
        if is_const { "const" } else { "" },
        if is_volatile { "volatile" } else { "" },
        reference.unwrap_or_default()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn java_type_normalization_matches_the_jvms_own_overload_rules() {
        assert_eq!(java_type_name("java.util.Map<String, String>"), "Map");
        assert_eq!(java_type_name("byte[]"), "byte[]");
        assert_eq!(java_type_name("List<String>[]"), "List[]");
        assert_eq!(java_type_name("int[][]"), "int[][]");
        assert_eq!(java_type_name("String []"), "String[]");
        assert_eq!(java_type_name("int"), "int");
        assert_eq!(java_type_name("T"), "T");
        // JSR-308 type annotations sit *inside* the type node, unlike the
        // parameter annotations the grammar keeps in a sibling `modifiers`.
        // Letting one through would move the symbol's persisted id whenever
        // somebody adds or removes `@NonNull` — the exact churn this
        // normalization exists to prevent (found reviewing this change).
        assert_eq!(java_type_name("java.util.@NonNull List<String>"), "List");
        assert_eq!(java_type_name("@NonNull String"), "String");
        assert_eq!(
            java_type_name("@NonNull String"),
            java_type_name("String"),
            "annotating a parameter type must not re-identify the method"
        );
    }
}
