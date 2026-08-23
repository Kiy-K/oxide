//! TypeScript / TSX symbol extraction via tree-sitter.

use super::LanguageExtractor;
use crate::symbols::{content_hash, Language, Symbol, SymbolKind};
use tree_sitter::{Node, Parser};

pub struct TsExtractor {
    pub tsx: bool,
}

fn first_line(text: &str) -> String {
    text.lines()
        .next()
        .unwrap_or("")
        .trim()
        .chars()
        .take(200)
        .collect()
}

impl TsExtractor {
    fn lang(&self) -> Language {
        if self.tsx {
            Language::Tsx
        } else {
            Language::TypeScript
        }
    }

    fn parse(&self, src: &str) -> Option<tree_sitter::Tree> {
        let mut parser = Parser::new();
        parser.set_language(&self.ts_language()).ok()?;
        parser.parse(src, None)
    }

    fn module_string(node: Node<'_>, src: &str) -> Option<String> {
        let t = node
            .child_by_field_name("source")?
            .utf8_text(src.as_bytes())
            .ok()?;
        Some(t.trim_matches(|c| c == '\'' || c == '"').to_string())
    }
}

impl LanguageExtractor for TsExtractor {
    fn language(&self) -> Language {
        self.lang()
    }

    fn ts_language(&self) -> tree_sitter::Language {
        if self.tsx {
            tree_sitter_typescript::LANGUAGE_TSX.into()
        } else {
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()
        }
    }

    fn collect_imports(&self, src: &str) -> Vec<String> {
        let Some(tree) = self.parse(src) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        Self::collect_from(tree.root_node(), src, &mut out);
        out.sort();
        out.dedup();
        out
    }

    fn extract(&self, file: &str, src: &str, imports: &[String]) -> Vec<Symbol> {
        let Some(tree) = self.parse(src) else {
            return Vec::new();
        };
        let mut symbols = Vec::new();
        Self::visit(
            tree.root_node(),
            file,
            src,
            self.lang(),
            imports,
            &mut Vec::new(),
            false,
            &mut symbols,
        );
        symbols
    }
}

impl TsExtractor {
    /// Import sources from `import ... from "x"` and `export ... from "x"`.
    fn collect_from(node: Node<'_>, src: &str, out: &mut Vec<String>) {
        match node.kind() {
            "import_statement" | "export_statement" => {
                if let Some(m) = Self::module_string(node, src) {
                    out.push(m);
                    return;
                }
            }
            _ => {}
        }
        let count = node.child_count();
        for i in 0..count {
            if let Some(child) = node.child(i) {
                Self::collect_from(child, src, out);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn visit(
        node: Node<'_>,
        file: &str,
        src: &str,
        lang: Language,
        imports: &[String],
        stack: &mut Vec<(String, bool)>,
        exported: bool,
        out: &mut Vec<Symbol>,
    ) {
        match node.kind() {
            "function_declaration" | "generator_function_declaration" => {
                Self::push(
                    node,
                    file,
                    src,
                    lang,
                    imports,
                    stack,
                    exported,
                    SymbolKind::Function,
                    out,
                );
                return;
            }
            "class_declaration" | "abstract_class_declaration" => {
                if let Some(q) = Self::push(
                    node,
                    file,
                    src,
                    lang,
                    imports,
                    stack,
                    exported,
                    SymbolKind::Class,
                    out,
                ) {
                    stack.push((q, true));
                    Self::visit_children(node, file, src, lang, imports, stack, exported, out);
                    stack.pop();
                }
                return;
            }
            "interface_declaration" => {
                Self::push(
                    node,
                    file,
                    src,
                    lang,
                    imports,
                    stack,
                    exported,
                    SymbolKind::Interface,
                    out,
                );
                return;
            }
            "type_alias_declaration" => {
                Self::push(
                    node,
                    file,
                    src,
                    lang,
                    imports,
                    stack,
                    exported,
                    SymbolKind::TypeAlias,
                    out,
                );
                return;
            }
            "enum_declaration" => {
                Self::push(
                    node,
                    file,
                    src,
                    lang,
                    imports,
                    stack,
                    exported,
                    SymbolKind::Enum,
                    out,
                );
                return;
            }
            "lexical_declaration" | "variable_declaration" => {
                let mut cur = node.walk();
                for declarator in node.children(&mut cur) {
                    if declarator.kind() != "variable_declarator" {
                        continue;
                    }
                    Self::handle_declarator(
                        declarator, file, src, lang, imports, stack, exported, out,
                    );
                }
                return;
            }
            "export_statement" => {
                Self::visit_children(node, file, src, lang, imports, stack, true, out);
                return;
            }
            _ => {}
        }
        Self::visit_children(node, file, src, lang, imports, stack, exported, out);
    }

    #[allow(clippy::too_many_arguments)]
    fn visit_children(
        node: Node<'_>,
        file: &str,
        src: &str,
        lang: Language,
        imports: &[String],
        stack: &mut Vec<(String, bool)>,
        exported: bool,
        out: &mut Vec<Symbol>,
    ) {
        let count = node.child_count();
        for i in 0..count {
            let Some(child) = node.child(i) else { continue };
            match child.kind() {
                "method_definition" | "function_signature" | "method_signature"
                    if matches!(stack.last(), Some((_, true))) =>
                {
                    Self::make_method(child, file, src, lang, imports, stack, out);
                }
                _ => Self::visit(child, file, src, lang, imports, stack, exported, out),
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn handle_declarator(
        declarator: Node<'_>,
        file: &str,
        src: &str,
        lang: Language,
        imports: &[String],
        stack: &mut [(String, bool)],
        exported: bool,
        out: &mut Vec<Symbol>,
    ) {
        let (Some(name_node), Some(value)) = (
            declarator.child_by_field_name("name"),
            declarator.child_by_field_name("value"),
        ) else {
            return;
        };
        let Ok(name) = name_node.utf8_text(src.as_bytes()) else {
            return;
        };
        if !name
            .chars()
            .all(|c| c.is_alphanumeric() || c == '_' || c == '$')
        {
            return; // skip destructured patterns
        }
        let kind = if matches!(
            value.kind(),
            "arrow_function" | "function_expression" | "function"
        ) {
            SymbolKind::Function
        } else if exported {
            SymbolKind::Constant
        } else {
            return; // plain internal consts are noise
        };
        let parent = stack.last().map(|(n, _)| n.clone());
        let qualified = match &parent {
            Some(p) => format!("{p}.{name}"),
            None => name.to_string(),
        };
        let start = declarator.start_position().row as u32 + 1;
        let end = declarator.end_position().row as u32 + 1;
        let body_src = span_lines(src, start, end);
        out.push(Symbol {
            qualified_name: qualified,
            name: name.to_string(),
            kind,
            language: lang,
            file: file.to_string(),
            start_line: start,
            end_line: end,
            content_hash: content_hash(&body_src),
            signature: first_line(&body_src),
            imports: imports.to_vec(),
            exported,
            parent,
            references: Vec::new(),
        });
    }

    /// Push one named declaration symbol; returns its qualified name.
    #[allow(clippy::too_many_arguments)]
    fn push(
        node: Node<'_>,
        file: &str,
        src: &str,
        lang: Language,
        imports: &[String],
        stack: &[(String, bool)],
        exported: bool,
        kind: SymbolKind,
        out: &mut Vec<Symbol>,
    ) -> Option<String> {
        let name_node = node.child_by_field_name("name")?;
        let name = name_node.utf8_text(src.as_bytes()).ok()?;
        let parent = stack.last().map(|(n, _)| n.clone());
        let qualified = match &parent {
            Some(p) => format!("{p}.{name}"),
            None => name.to_string(),
        };
        // Decorators: python wraps them (decorated_definition); TS grammars put
        // them as preceding sibling nodes of the declaration.
        let mut span_node = match node.parent() {
            Some(p) if p.kind() == "decorated_definition" => p,
            _ => node,
        };
        if let Some(p) = node.parent() {
            let mut cur = p.walk();
            let mut last_decorator: Option<Node> = None;
            for sib in p.children(&mut cur) {
                if sib.id() == node.id() {
                    break;
                }
                if sib.kind() == "decorator" {
                    last_decorator = Some(sib);
                }
            }
            if let Some(d) = last_decorator {
                span_node = d;
            }
        }
        let start = span_node.start_position().row as u32 + 1;
        let end = node.end_position().row as u32 + 1;
        let body_src = span_lines(src, start, end);
        out.push(Symbol {
            qualified_name: qualified.clone(),
            name: name.to_string(),
            kind,
            language: lang,
            file: file.to_string(),
            start_line: start,
            end_line: end,
            content_hash: content_hash(&body_src),
            signature: first_line(&body_src),
            imports: imports.to_vec(),
            exported,
            parent,
            references: Vec::new(),
        });
        Some(qualified)
    }

    fn make_method(
        node: Node<'_>,
        file: &str,
        src: &str,
        lang: Language,
        imports: &[String],
        stack: &[(String, bool)],
        out: &mut Vec<Symbol>,
    ) {
        let Some(name_node) = node.child_by_field_name("name") else {
            return;
        };
        let Ok(name) = name_node.utf8_text(src.as_bytes()) else {
            return;
        };
        let Some(parent) = stack.last().map(|(n, _)| n.clone()) else {
            return;
        };
        let qualified = format!("{parent}.{name}");
        let start = node.start_position().row as u32 + 1;
        let end = node.end_position().row as u32 + 1;
        let body_src = span_lines(src, start, end);
        out.push(Symbol {
            qualified_name: qualified,
            name: name.to_string(),
            kind: SymbolKind::Method,
            language: lang,
            file: file.to_string(),
            start_line: start,
            end_line: end,
            content_hash: content_hash(&body_src),
            signature: first_line(&body_src),
            imports: imports.to_vec(),
            exported: false,
            parent: Some(parent),
            references: Vec::new(),
        });
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
    use crate::parser::parse_file;

    const TS: &str = "\
import { Injectable } from '@nestjs/common';
import axios from 'axios';
export type RetryPolicy = { max: number };
export enum Mode { Fast, Slow }
export interface UserRepository {
  find_by_id(id: string): Promise<User>;
}
export const DEFAULT_TIMEOUT = 30;
const internal = 5;
export const refreshToken = async () => {
  return fetch('/refresh');
};
@Injectable()
export class AuthService {
  async login(user: string): Promise<boolean> {
    return true;
  }
  private hash(pw: string): number {
    return pw.length;
  }
}
function helper(x: number) {
  return x * 2;
}
";

    #[test]
    fn extracts_ts_declarations_with_export_flags() {
        let syms = parse_file("src/auth.ts", TS, Language::TypeScript);
        let find = |q: &str| syms.iter().find(|s| s.qualified_name == q).unwrap();
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        for q in [
            "RetryPolicy",
            "Mode",
            "UserRepository",
            "DEFAULT_TIMEOUT",
            "refreshToken",
            "AuthService",
            "AuthService.login",
            "AuthService.hash",
            "helper",
        ] {
            assert!(names.contains(&q), "{names:?}");
        }
        assert_eq!(find("RetryPolicy").kind, SymbolKind::TypeAlias);
        assert_eq!(find("Mode").kind, SymbolKind::Enum);
        assert_eq!(find("UserRepository").kind, SymbolKind::Interface);
        assert!(find("UserRepository").exported);
        assert_eq!(find("DEFAULT_TIMEOUT").kind, SymbolKind::Constant);
        assert!(!syms.iter().any(|s| s.name == "internal"));
        assert_eq!(find("refreshToken").kind, SymbolKind::Function);
        assert!(find("refreshToken").exported);
        assert_eq!(find("AuthService.login").kind, SymbolKind::Method);
        assert_eq!(
            find("AuthService.login").parent.as_deref(),
            Some("AuthService")
        );
        assert_eq!(find("helper").kind, SymbolKind::Function);
        assert_eq!(
            syms.iter()
                .find(|s| s.name == "__module__")
                .unwrap()
                .imports,
            vec!["@nestjs/common".to_string(), "axios".to_string()]
        );
        // Decorator line included in class span.
        let class = find("AuthService");
        let src_lines: Vec<&str> = TS.lines().collect();
        assert!(src_lines[class.start_line as usize - 1].contains("@Injectable()"));
    }

    #[test]
    fn extracts_tsx_components() {
        let src = "\
import { useState } from 'react';
export function Counter({ start }: Props) {
  const [n, setN] = useState(start);
  return <button onClick={() => setN(n + 1)}>{n}</button>;
}
export const Header = () => <h1>hi</h1>;
";
        let syms = parse_file("src/ui/Counter.tsx", src, Language::Tsx);
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"Counter"), "{names:?}");
        assert!(names.contains(&"Header"), "{names:?}");
        assert!(syms.iter().all(|s| s.language == Language::Tsx));
    }

    #[test]
    fn skips_d_ts_files_at_scanner_level() {
        assert!(crate::scanner::language_for_path(std::path::Path::new("x.d.ts")).is_none());
        assert!(crate::scanner::language_for_path(std::path::Path::new("x.ts")).is_some());
    }
}
