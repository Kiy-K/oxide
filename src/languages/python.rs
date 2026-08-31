//! Python symbol extraction via tree-sitter.

use super::LanguageExtractor;
use crate::symbols::{content_hash, Language, Symbol, SymbolKind};
use tree_sitter::{Node, Parser};

pub struct PythonExtractor;

fn first_line(text: &str) -> String {
    text.lines()
        .next()
        .unwrap_or("")
        .trim()
        .chars()
        .take(200)
        .collect()
}

impl LanguageExtractor for PythonExtractor {
    fn language(&self) -> Language {
        Language::Python
    }

    fn ts_language(&self) -> tree_sitter::Language {
        tree_sitter_python::LANGUAGE.into()
    }

    fn collect_imports(&self, src: &str) -> Vec<String> {
        let Some(tree) = self.parse(src) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        collect_from(tree.root_node(), src, &mut out);
        out.sort();
        out.dedup();
        out
    }

    fn extract(&self, file: &str, src: &str, imports: &[String]) -> Vec<Symbol> {
        let Some(tree) = self.parse(src) else {
            return Vec::new();
        };
        let mut symbols = Vec::new();
        visit(
            tree.root_node(),
            file,
            src,
            imports,
            &mut Vec::new(),
            &mut symbols,
        );
        symbols
    }
}

impl PythonExtractor {
    fn parse(&self, src: &str) -> Option<tree_sitter::Tree> {
        let mut parser = Parser::new();
        parser.set_language(&self.ts_language()).ok()?;
        parser.parse(src, None)
    }
}

fn collect_from(node: Node<'_>, src: &str, out: &mut Vec<String>) {
    match node.kind() {
        "import_from_statement" => {
            if let Some(m) = node.child_by_field_name("module_name") {
                if let Ok(t) = m.utf8_text(src.as_bytes()) {
                    out.push(t.to_string());
                }
            }
            return;
        }
        "import_statement" => {
            let mut cur = node.walk();
            for child in node.children(&mut cur) {
                match child.kind() {
                    "dotted_name" => {
                        if let Ok(t) = child.utf8_text(src.as_bytes()) {
                            out.push(t.to_string());
                        }
                    }
                    "aliased_import" => {
                        if let Some(n) = child.child_by_field_name("name") {
                            if let Ok(t) = n.utf8_text(src.as_bytes()) {
                                out.push(t.to_string());
                            }
                        }
                    }
                    _ => {}
                }
            }
            return;
        }
        _ => {}
    }
    let mut cur = node.walk();
    for child in node.children(&mut cur) {
        collect_from(child, src, out);
    }
}

/// Stack entries carry whether the frame is a class (methods live under classes).
type Frame = (String, bool);

fn visit(
    node: Node<'_>,
    file: &str,
    src: &str,
    imports: &[String],
    stack: &mut Vec<Frame>,
    out: &mut Vec<Symbol>,
) {
    match node.kind() {
        "function_definition" | "class_definition" => {
            let is_class = node.kind() == "class_definition";
            let Some(name_node) = node.child_by_field_name("name") else {
                return;
            };
            let Ok(name) = name_node.utf8_text(src.as_bytes()) else {
                return;
            };
            let kind = if is_class {
                SymbolKind::Class
            } else if matches!(stack.last(), Some((_, true))) {
                SymbolKind::Method
            } else {
                SymbolKind::Function
            };
            let parent = stack.last().map(|(n, _)| n.clone());
            let qualified = match &parent {
                Some(p) => format!("{p}.{name}"),
                None => name.to_string(),
            };
            // Decorated definitions span from the first decorator line.
            let span_node = match node.parent() {
                Some(p) if p.kind() == "decorated_definition" => p,
                _ => node,
            };
            let start = span_node.start_position().row as u32 + 1;
            let end = span_node.end_position().row as u32 + 1;
            let body_src = span_lines(src, start, end);
            out.push(Symbol {
                qualified_name: qualified.clone(),
                name: name.to_string(),
                kind,
                language: Language::Python,
                file: file.to_string(),
                start_line: start,
                end_line: end,
                content_hash: content_hash(&body_src),
                signature: first_line(&body_src),
                imports: imports.to_vec(),
                exported: true,
                parent,
                references: Vec::new(),
            });
            stack.push((qualified.clone(), is_class));
            if let Some(body) = node.child_by_field_name("body") {
                let mut bc = body.walk();
                for child in body.children(&mut bc) {
                    visit(child, file, src, imports, stack, out);
                }
            }
            stack.pop();
        }
        _ => {
            let count = node.child_count();
            for i in 0..count {
                if let Some(child) = node.child(i) {
                    visit(child, file, src, imports, stack, out);
                }
            }
        }
    }
}

fn span_lines(src: &str, start: u32, end: u32) -> String {
    src.lines()
        .skip(start.saturating_sub(1) as usize)
        .take(end.saturating_sub(start - 1) as usize)
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::{extractor_for_handwritten, parse_file_with};

    const SRC: &str = "\
import os
from collections import OrderedDict

@dataclass
class VersionedStore:
    '''Doc.'''
    def get(self, key):
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value

def module_level():
    def inner():
        pass
    return inner
";

    #[test]
    fn extracts_classes_methods_functions_and_imports() {
        // Pinned against the handwritten extractor explicitly (not
        // `parse_file`, whose default is `extractor_for` — see
        // `tags::tests::decorator_line_is_not_included_in_span` for the
        // documented gap this test's decorator-span assertion covers).
        let syms = parse_file_with(
            extractor_for_handwritten(Language::Python),
            "src/store.py",
            SRC,
            Language::Python,
        );
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"VersionedStore"), "{names:?}");
        assert!(names.contains(&"VersionedStore.get"), "{names:?}");
        assert!(names.contains(&"VersionedStore.put"), "{names:?}");
        assert!(names.contains(&"module_level"), "{names:?}");
        assert!(names.contains(&"module_level.inner"), "{names:?}");
        assert!(names.contains(&"src/store.py:__module__"));

        let cls = syms
            .iter()
            .find(|s| s.qualified_name == "VersionedStore")
            .unwrap();
        assert_eq!(cls.kind, SymbolKind::Class);
        // Span starts at the decorator line.
        assert_eq!(cls.start_line, 4);
        let get = syms
            .iter()
            .find(|s| s.qualified_name == "VersionedStore.get")
            .unwrap();
        assert_eq!(get.kind, SymbolKind::Method);
        assert_eq!((get.start_line, get.end_line), (7, 8));
        assert_eq!(
            get.span_text(SRC),
            "    def get(self, key):\n        return self.data[key]"
        );

        let inner = syms
            .iter()
            .find(|s| s.qualified_name == "module_level.inner")
            .unwrap();
        assert_eq!(inner.kind, SymbolKind::Function); // nested under function, not class
        let m = syms.iter().find(|s| s.name == "__module__").unwrap();
        assert_eq!(m.imports, vec!["collections".to_string(), "os".to_string()]);
    }

    #[test]
    fn import_collection_handles_alias_and_relative() {
        let src = "import numpy as np\nfrom .utils import helper\nfrom ..pkg.mod import thing\n";
        let imports = PythonExtractor.collect_imports(src);
        assert_eq!(imports, vec!["..pkg.mod", ".utils", "numpy"]);
    }
}
