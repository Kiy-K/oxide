//! Core symbol model shared across the indexing pipeline.

use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    Python,
    TypeScript,
    Tsx,
}

impl Language {
    pub fn as_str(&self) -> &'static str {
        match self {
            Language::Python => "python",
            Language::TypeScript => "typescript",
            Language::Tsx => "tsx",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SymbolKind {
    Module,
    Class,
    Function,
    Method,
    Interface,
    TypeAlias,
    Enum,
    Constant,
    Import,
}

impl fmt::Display for SymbolKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            SymbolKind::Module => "module",
            SymbolKind::Class => "class",
            SymbolKind::Function => "function",
            SymbolKind::Method => "method",
            SymbolKind::Interface => "interface",
            SymbolKind::TypeAlias => "type_alias",
            SymbolKind::Enum => "enum",
            SymbolKind::Constant => "constant",
            SymbolKind::Import => "import",
        };
        f.write_str(s)
    }
}

/// One extracted code entity. Spans are 1-based inclusive line numbers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Symbol {
    /// Qualified name, e.g. `VersionedStore.get` or `src/store.ts.refreshToken`.
    pub qualified_name: String,
    /// Bare declaration name (last path segment), e.g. `get`.
    pub name: String,
    pub kind: SymbolKind,
    pub language: Language,
    /// Repo-relative path with forward slashes.
    pub file: String,
    pub start_line: u32,
    pub end_line: u32,
    /// Stable hash of the symbol source text; drives incremental re-embedding.
    pub content_hash: u64,
    /// Signature / first meaningful line for display and lexical search.
    pub signature: String,
    /// Raw imported module paths declared in the enclosing file (deduped).
    pub imports: Vec<String>,
    pub exported: bool,
    /// Parent symbol qualified name within the same file, if nested.
    pub parent: Option<String>,
}

impl Symbol {
    /// Stable identity for persistence: path + qualified name.
    pub fn id(&self) -> u64 {
        fnv1a64_iter([self.file.as_bytes(), [0].as_slice(), self.qualified_name.as_bytes()])
    }

    pub fn span_text<'a>(&self, src: &'a str) -> &'a str {
        let mut start_byte = 0usize;
        let mut end_byte = src.len();
        let mut found_start = false;
        let mut offset = 0usize;
        for (i, line) in src.split('\n').enumerate() {
            let lineno = i + 1;
            if lineno == self.start_line as usize {
                start_byte = offset;
                found_start = true;
            }
            if lineno == self.end_line as usize + 1 {
                end_byte = offset.saturating_sub(1);
                break;
            }
            offset += line.len() + 1;
        }
        if !found_start {
            return "";
        }
        &src[start_byte..end_byte.clamp(start_byte, src.len())]
    }
}

/// Stable, persisted-safe 64-bit FNV-1a over length-prefixed byte strings
/// (prefixing keeps `["ab"]` != `["a", "b"]`).
pub fn fnv1a64_iter(parts: impl IntoIterator<Item = impl AsRef<[u8]>>) -> u64 {
    fn mix(h: &mut u64, bytes: &[u8]) {
        for b in bytes {
            *h ^= *b as u64;
            *h = h.wrapping_mul(0x100000001b3);
        }
    }
    let mut h: u64 = 0xcbf29ce484222325;
    for part in parts {
        mix(&mut h, &(part.as_ref().len() as u64).to_le_bytes());
        mix(&mut h, part.as_ref());
    }
    h
}

/// Hash of a source text blob (per-symbol or per-file).
pub fn content_hash(text: &str) -> u64 {
    fnv1a64_iter([text.as_bytes()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fnv_is_stable_and_order_sensitive() {
        assert_eq!(content_hash("foo"), content_hash("foo"));
        assert_ne!(content_hash("foo"), content_hash("bar"));
        assert_ne!(fnv1a64_iter(["ab"]), fnv1a64_iter(["a", "b"]));
    }

    #[test]
    fn symbol_id_uses_path_and_name() {
        let mk = |file: &str, name: &str| Symbol {
            qualified_name: name.into(),
            name: name.into(),
            kind: SymbolKind::Function,
            language: Language::Python,
            file: file.into(),
            start_line: 1,
            end_line: 2,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: false,
            parent: None,
        };
        assert_eq!(mk("a.py", "f").id(), mk("a.py", "f").id());
        assert_ne!(mk("a.py", "f").id(), mk("b.py", "f").id());
        assert_ne!(mk("a.py", "f").id(), mk("a.py", "g").id());
    }

    #[test]
    fn span_text_slices_source() {
        let src = "def foo():\n    pass\n\ndef bar():\n    pass\n";
        let sym = Symbol {
            qualified_name: "bar".into(),
            name: "bar".into(),
            kind: SymbolKind::Function,
            language: Language::Python,
            file: "x.py".into(),
            start_line: 4,
            end_line: 5,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: false,
            parent: None,
        };
        assert_eq!(sym.span_text(src), "def bar():\n    pass");
    }
}
