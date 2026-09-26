/// Shared source-text tokenizer used by lexical search and embeddings:
/// splits camelCase/snake_case/kebab-case identifiers and path segments,
/// lowercases, drops stopwords and single characters.
pub fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    tokenize_into(text, &mut |t| out.push(t.to_string()));
    out
}

/// Allocation-light tokenizer core: emits each token to `emit` without building
/// intermediate vectors. Tokens are borrowed slices of `text` whenever no case
/// folding is needed (the common case for code identifiers).
pub fn tokenize_into(text: &str, emit: &mut dyn FnMut(&str)) {
    for raw in text.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if raw.is_empty() {
            continue;
        }
        split_identifier_into(raw, &mut |part: &str, needs_lower: bool| {
            // Fast path: already-lowercase tokens pass through borrowed.
            if needs_lower {
                let t = part.to_lowercase();
                if t.len() >= 2 && !STOPWORDS.contains(&t.as_str()) {
                    emit(&t);
                }
            } else if part.len() >= 2 && !STOPWORDS.contains(&part) {
                emit(part);
            }
        });
    }
}

const STOPWORDS: &[&str] = &[
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "self",
    "none",
    "null",
    "undefined",
    "true",
    "false",
    "fn",
    "func",
    "def",
    "let",
    "var",
    "const",
    "return",
    "import",
];

/// Splits snake_case / kebab-case / camelCase identifiers, emitting subtokens.
/// `needs_lower` tells the caller whether the slice contains uppercase chars.
fn split_identifier_into(raw: &str, emit: &mut dyn FnMut(&str, bool)) {
    let bytes = raw.as_bytes();
    let mut seg_start = 0usize;
    let mut seg_upper = false;
    for i in 0..bytes.len() {
        let b = bytes[i];
        if b == b'_' || b == b'-' || b == b'.' {
            if i > seg_start {
                emit(&raw[seg_start..i], seg_upper);
            }
            seg_start = i + 1;
            seg_upper = false;
            continue;
        }
        // camelCase boundary: lowercase→Upper starts a new token.
        if b.is_ascii_uppercase() && i > seg_start && !bytes[i - 1].is_ascii_uppercase() {
            emit(&raw[seg_start..i], seg_upper);
            seg_start = i;
            seg_upper = false;
        }
        seg_upper |= b.is_ascii_uppercase();
    }
    if raw.len() > seg_start {
        emit(&raw[seg_start..], seg_upper);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tokenizer_splits_cases_and_drops_stopwords() {
        assert_eq!(
            tokenize("RetryPolicy.handle_request"),
            vec!["retry", "policy", "handle", "request"]
        );
        assert_eq!(tokenize("the self a"), Vec::<String>::new());
        assert!(tokenize("src/authService.ts refresh_token").contains(&"refresh".to_string()));
    }
}
