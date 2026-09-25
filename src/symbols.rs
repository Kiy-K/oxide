//! Core symbol model shared across the indexing pipeline.

use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    Python,
    TypeScript,
    Tsx,
    /// JavaScript and JSX, both parsed with the TSX grammar — see
    /// `languages::JAVASCRIPT_PROFILE`.
    JavaScript,
    Rust,
    Go,
    Java,
    Ruby,
    Php,
    C,
    Cpp,
    /// Whole-file documentation (`.md`), not a programming language: no
    /// grammar, no declarations, no structural relations. A markdown file
    /// always produces exactly one symbol — the same whole-file module
    /// fallback every other language already produces for a comment-only
    /// file (`parser.rs::parse_file_with`) — so it rides the existing
    /// lexical/semantic/incremental machinery unmodified. See
    /// [`Language::has_structural_queries`].
    Markdown,
}

impl Language {
    /// Every language this build extracts. The one list — `oxide status`
    /// reports it and `tree_sitter_structural`'s query-compile test iterates
    /// it — because a hand-maintained second copy is exactly how `oxide
    /// status` came to keep claiming "python, typescript, tsx" after Rust
    /// and Go shipped. `Markdown` belongs here too: `from_str`/`as_str`
    /// round-tripping (SQLite's persisted `language` column) needs every
    /// persistable value in this one list, same as every other entry.
    pub const ALL: &'static [Language] = &[
        Language::Python,
        Language::TypeScript,
        Language::Tsx,
        Language::JavaScript,
        Language::Rust,
        Language::Go,
        Language::Java,
        Language::Ruby,
        Language::Php,
        Language::C,
        Language::Cpp,
        Language::Markdown,
    ];

    /// Whether this language has a real tree-sitter grammar and
    /// callers/implementors queries (`tree_sitter_structural.rs`). Only
    /// `Markdown` is excluded — it has no AST to query, by design, not by
    /// omission. `structural_relations::compute_file_relations` checks this
    /// before calling into `tree_sitter_structural`, and the query-compile
    /// conformance test filters on it too, so neither one ever needs a
    /// grammar that doesn't exist.
    pub fn has_structural_queries(&self) -> bool {
        !matches!(self, Language::Markdown)
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Language::Python => "python",
            Language::TypeScript => "typescript",
            Language::Tsx => "tsx",
            Language::JavaScript => "javascript",
            Language::Rust => "rust",
            Language::Go => "go",
            Language::Java => "java",
            Language::Ruby => "ruby",
            Language::Php => "php",
            Language::C => "c",
            Language::Cpp => "cpp",
            Language::Markdown => "markdown",
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

impl std::str::FromStr for Language {
    type Err = anyhow::Error;
    /// Derived from [`Language::ALL`] and [`Language::as_str`] rather than a
    /// second hand-written match: the decode side used to be its own
    /// `match` with a `_ => TypeScript` catch-all, so every Rust and Go
    /// symbol read back out of SQLite came back labelled TypeScript.
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Language::ALL
            .iter()
            .copied()
            .find(|l| l.as_str() == s)
            .ok_or_else(|| anyhow::anyhow!("unknown language: {s}"))
    }
}

impl std::str::FromStr for SymbolKind {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(match s {
            "module" => SymbolKind::Module,
            "class" => SymbolKind::Class,
            "function" => SymbolKind::Function,
            "method" => SymbolKind::Method,
            "interface" => SymbolKind::Interface,
            "type_alias" => SymbolKind::TypeAlias,
            "enum" => SymbolKind::Enum,
            "constant" => SymbolKind::Constant,
            "import" => SymbolKind::Import,
            _ => anyhow::bail!("unknown kind: {s}"),
        })
    }
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
    /// Resolved references to other known symbols (filled by the indexer).
    #[serde(default)]
    pub references: Vec<String>,
    /// AST-precise call-target bare names found in this symbol's body —
    /// experimental, populated only by `structural_relations`'s opt-in
    /// second pass (`docs/precomputed-structural-relations/README.md`),
    /// never by the default `update_index` path. Deliberately excluded
    /// from `embeddings::symbol_embed_text` — unlike `references`, these do not affect
    /// `content_hash` or the embedding, keeping embeddings frozen per that
    /// experiment's brief. Bare names only, same heuristic tier as
    /// `references`/`uses` (no scope analysis) — not a resolved call graph.
    #[serde(default)]
    pub calls: Vec<String>,
    /// AST-precise base class/interface bare names this symbol (a class)
    /// extends or implements — same provenance and caveats as `calls`.
    #[serde(default)]
    pub bases: Vec<String>,
    /// Whether `imports`/`references` are loaded. Only the corpus snapshot
    /// loader ([`crate::storage::IndexBackend::all_symbols_lean`]) produces
    /// [`Completeness::Partial`] symbols; every symbol that leaves the
    /// snapshot as a `neighbors()` seed or as output goes through
    /// `retrieval::complete_symbols` first. Never serialized for a complete
    /// symbol (output stays byte-identical); serializing a partial one is an
    /// error, and `RelationGraph::neighbors` / `LexicalIndex::build` assert
    /// against one — a partial symbol cannot pass for a complete one.
    #[serde(
        default,
        skip_deserializing,
        skip_serializing_if = "Completeness::is_complete"
    )]
    pub completeness: Completeness,
}

/// See [`Symbol::completeness`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Completeness {
    #[default]
    Complete,
    /// `imports` is empty and `references` is loaded only for test symbols
    /// ([`is_test_symbol`]) — exactly what `RelationGraph` reads of a
    /// non-seed symbol (docs/retrieval-profile/corpus-load-baseline/
    /// lean-snapshot-screen/).
    Partial,
}

impl Completeness {
    pub fn is_complete(&self) -> bool {
        *self == Completeness::Complete
    }
}

/// Only reached for a [`Completeness::Partial`] symbol (a complete one skips
/// the field), and always fails: emitting a lean symbol's empty `imports`/
/// `references` as if they were real would be silently wrong output.
impl Serialize for Completeness {
    fn serialize<S: serde::Serializer>(&self, _: S) -> Result<S::Ok, S::Error> {
        Err(serde::ser::Error::custom(
            "a partial (lean-snapshot) Symbol was serialized without \
             retrieval::complete_symbols",
        ))
    }
}

/// Test-symbol classification shared by `RelationGraph::related_tests` and
/// the lean corpus loader, which must agree exactly: the loader keeps
/// `references` for precisely the symbols this accepts. `buf` is scratch
/// reused across calls — lowercasing into two fresh `String`s per symbol
/// was the bulk of `RelationIndex::build`'s allocations. ASCII input takes
/// the same bulk byte-wise path `str::to_lowercase` uses internally;
/// anything else falls back to `to_lowercase` itself, so the mapping is
/// identical.
pub fn is_test_symbol(
    file: &str,
    name: &str,
    kind: SymbolKind,
    buf: &mut (String, String),
) -> bool {
    fn lower_into(src: &str, dst: &mut String) {
        if src.is_ascii() {
            dst.clear();
            dst.push_str(src);
            dst.make_ascii_lowercase();
        } else {
            *dst = src.to_lowercase();
        }
    }
    let (f, n) = buf;
    lower_into(file, f);
    lower_into(name, n);
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || f.contains("\\tests\\")
        || n.starts_with("test_")
        || n.ends_with("_test")
        || n.ends_with("test") && (matches!(kind, SymbolKind::Function | SymbolKind::Method))
}

impl Symbol {
    pub fn is_complete(&self) -> bool {
        self.completeness.is_complete()
    }

    /// Stable identity for persistence: path + qualified name.
    pub fn id(&self) -> u64 {
        fnv1a64_iter([
            self.file.as_bytes(),
            [0].as_slice(),
            self.qualified_name.as_bytes(),
        ])
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
            references: Vec::new(),
            calls: Vec::new(),
            bases: Vec::new(),
            completeness: Default::default(),
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
            references: Vec::new(),
            calls: Vec::new(),
            bases: Vec::new(),
            completeness: Default::default(),
        };
        assert_eq!(sym.span_text(src), "def bar():\n    pass");
    }
}
