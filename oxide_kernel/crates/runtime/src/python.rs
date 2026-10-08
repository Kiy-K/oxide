//! Python syntax facts from the pinned tree-sitter grammar. This module only
//! reads syntax; identity, scoping and resolution are kernel policy
//! (`oxide_kernel::python`). Traversal is iterative, so deeply nested source
//! cannot overflow the stack.

use oxide_kernel::ingest::Facts;
use oxide_kernel::knowledge::ByteRange;
use oxide_kernel::python::{Decl, Import, ImportItem, ParsedFile, Scope, Use};
use tree_sitter::{Node, Parser};

/// Parser substrate versions; part of the derivation.
pub const GRAMMAR: &str = "tree-sitter=0.27.0 tree-sitter-python=0.25.0";

const MAX_DIAGNOSTICS: usize = 16;

/// One parser per derivation run; the grammar is loaded once.
pub struct PythonParser(Parser);

impl Default for PythonParser {
    fn default() -> Self {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("the pinned grammar matches the pinned tree-sitter ABI");
        Self(parser)
    }
}

impl PythonParser {
    pub fn parse(&mut self, bytes: &[u8]) -> Facts {
        // ponytail: PEP 263 coding declarations are not decoded; non-UTF-8
        // source is unsupported rather than guessed.
        if std::str::from_utf8(bytes).is_err() {
            return Facts::Unsupported("Python source is not UTF-8".into());
        }
        let Some(tree) = self.0.parse(bytes, None) else {
            return Facts::Unsupported("parser returned no tree".into());
        };
        let root = tree.root_node();
        let mut extractor = Extractor {
            src: bytes,
            file: ParsedFile {
                scopes: vec![Scope::default()],
                ..ParsedFile::default()
            },
        };
        extractor.walk(root);
        if root.has_error() {
            extractor.file.diagnostics = diagnostics(root);
        }
        Facts::Python(extractor.file)
    }
}

struct Extractor<'a> {
    src: &'a [u8],
    file: ParsedFile,
}

fn range(node: Node) -> ByteRange {
    ByteRange {
        start: node.start_byte() as u64,
        end: node.end_byte() as u64,
    }
}

impl Extractor<'_> {
    fn text(&self, node: Node) -> String {
        node.utf8_text(self.src).unwrap_or_default().to_owned()
    }

    fn bind(&mut self, scope: usize, node: Option<Node>) {
        if let Some(node) = node {
            let names = targets(node)
                .into_iter()
                .map(|n| self.text(n))
                .collect::<Vec<_>>();
            self.file.scopes[scope].bound.extend(names);
        }
    }

    /// `identifier` or an attribute chain over one, as written.
    fn dotted(&self, mut node: Node) -> Option<String> {
        let mut parts = Vec::new();
        while node.kind() == "attribute" {
            parts.push(self.text(node.child_by_field_name("attribute")?));
            node = node.child_by_field_name("object")?;
        }
        (node.kind() == "identifier").then(|| {
            parts.push(self.text(node));
            parts.reverse();
            parts.join(".")
        })
    }

    fn segments(&self, dotted_name: Node) -> Vec<String> {
        named(dotted_name)
            .into_iter()
            .map(|n| self.text(n))
            .collect()
    }

    fn walk(&mut self, root: Node) {
        // (node, scope, start of an enclosing decorated_definition)
        let mut stack = vec![(root, 0usize, None::<usize>)];
        while let Some((node, scope, decorated)) = stack.pop() {
            let mut next: Vec<(Node, usize, Option<usize>)> = Vec::new();
            match node.kind() {
                "decorated_definition" => {
                    let definition = node.child_by_field_name("definition");
                    for child in named(node) {
                        let start = (Some(child) == definition).then_some(node.start_byte());
                        next.push((child, scope, start));
                    }
                }
                "function_definition" | "class_definition" => {
                    match self.declaration(node, scope, decorated) {
                        Some(children) => next.extend(children),
                        // A declaration without a name (error recovery) is
                        // not a symbol; its contents stay in this scope.
                        None => next.extend(named(node).into_iter().map(|c| (c, scope, None))),
                    }
                }
                "call" => {
                    if let Some(name) = node
                        .child_by_field_name("function")
                        .and_then(|f| self.dotted(f))
                    {
                        self.file.scopes[scope].calls.push(Use {
                            name,
                            range: range(node),
                        });
                    }
                    next.extend(named(node).into_iter().map(|c| (c, scope, None)));
                }
                "import_statement" => {
                    for name in fields(node, "name") {
                        let (dotted, alias) = self.aliased(name);
                        self.file.scopes[scope].imports.push(Import {
                            level: 0,
                            module: dotted,
                            item: ImportItem::Module { alias },
                            range: range(name),
                        });
                    }
                }
                "import_from_statement" => self.import_from(node, scope),
                "future_import_statement" => {}
                "global_statement" | "nonlocal_statement" => {
                    let names = named(node)
                        .into_iter()
                        .map(|n| self.text(n))
                        .collect::<Vec<_>>();
                    self.file.rebound.extend(names);
                }
                kind => {
                    match kind {
                        "assignment"
                        | "augmented_assignment"
                        | "for_statement"
                        | "for_in_clause"
                        | "type_alias_statement" => {
                            self.bind(scope, node.child_by_field_name("left"));
                        }
                        "named_expression" => self.bind(scope, node.child_by_field_name("name")),
                        "as_pattern" | "except_clause" => {
                            self.bind(scope, node.child_by_field_name("alias"));
                        }
                        "delete_statement" | "case_pattern" => self.bind(scope, Some(node)),
                        "lambda" => self.bind(scope, node.child_by_field_name("parameters")),
                        _ => {}
                    }
                    next.extend(named(node).into_iter().map(|c| (c, scope, None)));
                }
            }
            stack.extend(next.into_iter().rev());
        }
    }

    /// Opens the declaration's scope; returns the children to visit, in
    /// source order, with the scope each is evaluated in.
    fn declaration<'t>(
        &mut self,
        node: Node<'t>,
        scope: usize,
        decorated: Option<usize>,
    ) -> Option<Vec<(Node<'t>, usize, Option<usize>)>> {
        let name = self.text(node.child_by_field_name("name")?);
        if name.is_empty() {
            return None;
        }
        let class = node.kind() == "class_definition";
        let body = node.child_by_field_name("body");
        let header_end = body.map_or(node.end_byte(), |b| b.start_byte());
        let header = &self.src[node.start_byte()..header_end];
        let signature = String::from_utf8_lossy(header)
            .trim()
            .trim_end_matches(':')
            .trim_end()
            .to_owned();
        let own = self.file.scopes.len();
        self.file.scopes.push(Scope {
            parent: Some(scope),
            decl: Some(Decl {
                class,
                name,
                range: ByteRange {
                    start: decorated.unwrap_or(node.start_byte()) as u64,
                    end: node.end_byte() as u64,
                },
                signature,
            }),
            ..Scope::default()
        });
        let mut next = Vec::new();
        if class {
            if let Some(arguments) = node.child_by_field_name("superclasses") {
                for base in named(arguments) {
                    if let Some(name) = self.dotted(base) {
                        self.file.scopes[own].bases.push(Use {
                            name,
                            range: range(base),
                        });
                    }
                }
                next.push((arguments, scope, None));
            }
        } else {
            if let Some(parameters) = node.child_by_field_name("parameters") {
                for parameter in named(parameters) {
                    // Defaults and annotations run in the enclosing scope;
                    // parameter names bind in the function's own scope.
                    for field in ["value", "type"] {
                        if let Some(child) = parameter.child_by_field_name(field) {
                            next.push((child, scope, None));
                        }
                    }
                    match parameter.kind() {
                        "default_parameter" | "typed_default_parameter" => {
                            self.bind(own, parameter.child_by_field_name("name"));
                        }
                        "typed_parameter" => {
                            let annotation = parameter.child_by_field_name("type");
                            for child in named(parameter) {
                                if Some(child) != annotation {
                                    self.bind(own, Some(child));
                                }
                            }
                        }
                        _ => self.bind(own, Some(parameter)),
                    }
                }
            }
            if let Some(returns) = node.child_by_field_name("return_type") {
                next.push((returns, scope, None));
            }
        }
        if let Some(body) = body {
            next.push((body, own, None));
        }
        Some(next)
    }

    fn aliased(&self, node: Node) -> (Vec<String>, Option<String>) {
        if node.kind() == "aliased_import" {
            let dotted = node
                .child_by_field_name("name")
                .map(|n| self.segments(n))
                .unwrap_or_default();
            let alias = node.child_by_field_name("alias").map(|n| self.text(n));
            (dotted, alias)
        } else {
            (self.segments(node), None)
        }
    }

    fn import_from(&mut self, node: Node, scope: usize) {
        let Some(source) = node.child_by_field_name("module_name") else {
            return;
        };
        let (level, module) = if source.kind() == "relative_import" {
            let mut level = 0;
            let mut module = Vec::new();
            for child in named(source) {
                match child.kind() {
                    "import_prefix" => level = self.text(child).matches('.').count() as u32,
                    _ => module = self.segments(child),
                }
            }
            (level, module)
        } else {
            (0, self.segments(source))
        };
        let mut items = Vec::new();
        if named(node).iter().any(|c| c.kind() == "wildcard_import") {
            items.push((ImportItem::Star, range(node)));
        }
        for name in fields(node, "name") {
            let (dotted, alias) = self.aliased(name);
            let name_range = range(name);
            items.push((
                ImportItem::Name {
                    name: dotted.join("."),
                    alias,
                },
                name_range,
            ));
        }
        for (item, range) in items {
            self.file.scopes[scope].imports.push(Import {
                level,
                module: module.clone(),
                item,
                range,
            });
        }
    }
}

fn named(node: Node) -> Vec<Node> {
    let mut cursor = node.walk();
    node.named_children(&mut cursor).collect()
}

fn fields<'t>(node: Node<'t>, field: &str) -> Vec<Node<'t>> {
    let mut cursor = node.walk();
    node.children_by_field_name(field, &mut cursor).collect()
}

/// Identifiers a binding target binds. Attribute and subscript targets bind
/// nothing; any other shape is searched for identifiers, which may over-
/// approximate (and so only make resolution more conservative).
fn targets(node: Node) -> Vec<Node> {
    let mut found = Vec::new();
    let mut stack = vec![node];
    while let Some(node) = stack.pop() {
        match node.kind() {
            "identifier" => found.push(node),
            "attribute" | "subscript" => {}
            _ => stack.extend(named(node)),
        }
    }
    found
}

fn diagnostics(root: Node) -> Vec<String> {
    let mut found = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        if node.is_missing() {
            found.push(format!(
                "missing {} at byte {}",
                node.kind(),
                node.start_byte()
            ));
        } else if node.is_error() {
            found.push(format!(
                "syntax error at bytes {}..{}",
                node.start_byte(),
                node.end_byte()
            ));
        }
        if node.has_error() {
            let mut cursor = node.walk();
            let children: Vec<Node> = node.children(&mut cursor).collect();
            stack.extend(children.into_iter().rev());
        }
    }
    if found.len() > MAX_DIAGNOSTICS {
        let omitted = found.len() - MAX_DIAGNOSTICS;
        found.truncate(MAX_DIAGNOSTICS);
        found.push(format!("{omitted} more syntax errors omitted"));
    }
    found
}
