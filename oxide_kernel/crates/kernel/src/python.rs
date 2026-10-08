//! Python language slice (Phase 2B): per-file syntax facts from the runtime
//! parser become symbols, physical/logical containment and conservative
//! imports, calls, base-class references and test associations.
//!
//! Conventions (ADR-0010 left them to this slice):
//!
//! - `ModuleId` is `python:` plus the dotted path of a `.py` file relative to
//!   the repository root (`a/b.py` and `a/b/__init__.py` are both `python:a.b`).
//!   A file with a segment that is not an identifier has no module. Source
//!   roots and `sys.path` are not modeled.
//! - Symbols are `class` and `def` declarations (kinds `class`, `function`,
//!   `method`), nested by declaration only; control-flow blocks are
//!   transparent. Lambdas and comprehensions are expressions, so Python needs
//!   no synthetic names. A decorated declaration's range includes its
//!   decorators. Python cannot put two declarations on one line, so same-line
//!   nesting does not arise.
//! - Tests follow pytest's default discovery: in `test_*.py` / `*_test.py`,
//!   top-level `test*` functions, top-level `Test*` classes and their `test*`
//!   methods carry the test facet. A test calling a resolved production
//!   symbol yields a `Heuristic` `TESTED_BY` edge.
//!
//! Resolution follows Python scoping (a scope's own names, enclosing function
//! scopes but never enclosing class bodies, then the module) and only
//! resolves plain names to declarations, submodules or `from` imports of
//! them. Any other binding of the name in the deciding scope (assignment,
//! parameter, loop target, star import, unresolved import, or `global`/
//! `nonlocal` anywhere in the file) makes it unresolved; several candidates
//! make it ambiguous. Attribute calls, builtins and re-exported imports stay
//! unresolved. There is no type inference and no compiler-level claim.

use std::collections::{BTreeMap, BTreeSet};

use crate::id::{Digest, EntityId, ModuleId, RepoPath, Segment, SymbolId};
use crate::ingest::contains;
use crate::knowledge::{
    Basis, Batch, ByteRange, Containment, Entity, Relation, RelationKind, SourceRef, Target,
};

/// Version of these extraction and resolution rules; part of the derivation.
pub const VERSION: &str = "oxide-python-v1";

/// Syntax facts of one Python file, produced by the runtime parser.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ParsedFile {
    /// `scopes[0]` is the module scope. Every other scope is opened by its
    /// declaration and names an earlier scope as its parent.
    pub scopes: Vec<Scope>,
    /// Names declared `global` or `nonlocal` anywhere in the file; they never
    /// resolve.
    pub rebound: Vec<String>,
    /// Syntax errors. Non-empty means the file is only partially understood.
    pub diagnostics: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Scope {
    pub parent: Option<usize>,
    pub decl: Option<Decl>,
    /// Names bound here by anything but a declaration or an import. Over-
    /// approximating only costs resolution, never correctness.
    pub bound: Vec<String>,
    pub imports: Vec<Import>,
    pub calls: Vec<Use>,
    /// A class declaration's base expressions, evaluated in its parent scope.
    pub bases: Vec<Use>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Decl {
    pub class: bool,
    pub name: String,
    pub range: ByteRange,
    pub signature: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Import {
    /// Leading dots of a relative import; 0 is absolute.
    pub level: u32,
    pub module: Vec<String>,
    pub item: ImportItem,
    pub range: ByteRange,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ImportItem {
    /// `import a.b` binds `a`; `import a.b as x` binds `x` to `a.b`.
    Module { alias: Option<String> },
    /// `from m import name [as alias]`.
    Name { name: String, alias: Option<String> },
    /// `from m import *`.
    Star,
}

/// A referenced name: an identifier or dotted attribute chain, as written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Use {
    pub name: String,
    pub range: ByteRange,
}

enum Res {
    /// One or more candidate entities, sorted and distinct.
    Found(Vec<EntityId>),
    /// Unknown, dynamic or shadowed.
    Opaque,
}

struct File<'a> {
    path: &'a RepoPath,
    digest: &'a Digest,
    parsed: &'a ParsedFile,
    ids: Vec<Option<SymbolId>>,
    children: Vec<Vec<usize>>,
    module: Option<String>,
    init: bool,
}

struct Context<'a> {
    files: Vec<File<'a>>,
    modules: BTreeMap<String, Vec<usize>>,
    tests: BTreeSet<SymbolId>,
}

pub(crate) fn derive(
    files: &BTreeMap<&RepoPath, (&Digest, &ParsedFile)>,
    batch: &mut Batch,
) -> Result<(), String> {
    let mut ctx = Context {
        files: Vec::new(),
        modules: BTreeMap::new(),
        tests: BTreeSet::new(),
    };
    for (&path, &(digest, parsed)) in files {
        let (ids, children) = symbol_ids(path, parsed)?;
        let module = module_name(path);
        if let Some(name) = &module {
            ctx.modules
                .entry(name.clone())
                .or_default()
                .push(ctx.files.len());
        }
        let file = File {
            path,
            digest,
            parsed,
            ids,
            children,
            module,
            init: path.as_str().rsplit('/').next() == Some("__init__.py"),
        };
        for k in 1..parsed.scopes.len() {
            if is_test(&file, k) {
                ctx.tests
                    .insert(file.ids[k].clone().expect("declaration scope"));
            }
        }
        ctx.files.push(file);
    }
    for (name, members) in &ctx.modules {
        let id = module_id(name);
        batch.entities.push(Entity {
            id: id.clone(),
            kind: "module".into(),
            name: name.clone(),
            signature: None,
            source: None,
            test: false,
        });
        batch
            .relations
            .push(contains(EntityId::Repository, id.clone()));
        for &i in members {
            batch.relations.push(Relation {
                kind: RelationKind::Contains(Containment::Logical),
                from: id.clone(),
                to: Target::Resolved(EntityId::File(ctx.files[i].path.clone())),
                basis: Basis::Syntactic,
                evidence: None,
            });
        }
    }
    for i in 0..ctx.files.len() {
        ctx.emit(i, batch);
    }
    Ok(())
}

/// Symbol IDs per scope (`None` for the module scope) and each scope's child
/// declarations. Ordinals count same-named siblings in byte order.
#[allow(clippy::type_complexity)]
fn symbol_ids(
    path: &RepoPath,
    parsed: &ParsedFile,
) -> Result<(Vec<Option<SymbolId>>, Vec<Vec<usize>>), String> {
    let scopes = &parsed.scopes;
    let bad = |what: &str| format!("{}: malformed Python facts: {what}", path.as_str());
    match scopes.first() {
        Some(Scope {
            parent: None,
            decl: None,
            ..
        }) => {}
        _ => return Err(bad("first scope is not the module")),
    }
    let mut children = vec![Vec::new(); scopes.len()];
    let mut siblings: BTreeMap<(usize, &str), Vec<(u64, usize)>> = BTreeMap::new();
    for (k, scope) in scopes.iter().enumerate().skip(1) {
        let (Some(parent), Some(decl)) = (scope.parent, &scope.decl) else {
            return Err(bad("declaration scope without parent or declaration"));
        };
        if parent >= k || decl.name.is_empty() || decl.range.start > decl.range.end {
            return Err(bad("declaration out of order or empty"));
        }
        children[parent].push(k);
        siblings
            .entry((parent, decl.name.as_str()))
            .or_default()
            .push((decl.range.start, k));
    }
    let mut ordinals = vec![0u32; scopes.len()];
    for group in siblings.values_mut() {
        group.sort_unstable();
        for (ordinal, &(_, k)) in group.iter().enumerate() {
            ordinals[k] = u32::try_from(ordinal).map_err(|_| bad("too many siblings"))?;
        }
    }
    let mut ids: Vec<Option<SymbolId>> = vec![None];
    for (k, scope) in scopes.iter().enumerate().skip(1) {
        let parent = scope.parent.expect("checked");
        let mut segments = ids[parent]
            .as_ref()
            .map_or_else(Vec::new, |id| id.path().to_vec());
        segments.push(Segment {
            name: scope.decl.as_ref().expect("checked").name.clone(),
            ordinal: ordinals[k],
        });
        ids.push(Some(
            SymbolId::new(path.clone(), segments).map_err(|e| bad(&e.to_string()))?,
        ));
    }
    Ok((ids, children))
}

fn module_name(path: &RepoPath) -> Option<String> {
    let mut parts: Vec<&str> = path.as_str().strip_suffix(".py")?.split('/').collect();
    if parts.last() == Some(&"__init__") {
        parts.pop();
    }
    let identifier = |part: &&str| {
        let mut chars = part.chars();
        chars.next().is_some_and(|c| c == '_' || c.is_alphabetic())
            && chars.all(|c| c == '_' || c.is_alphanumeric())
    };
    (!parts.is_empty() && parts.iter().all(identifier)).then(|| parts.join("."))
}

fn module_id(name: &str) -> EntityId {
    EntityId::Module(ModuleId::new(format!("python:{name}")).expect("non-empty module name"))
}

fn is_test(file: &File, k: usize) -> bool {
    let name = file.path.as_str().rsplit('/').next().unwrap_or_default();
    if !(name.starts_with("test_") || name.ends_with("_test.py")) {
        return false;
    }
    let scopes = &file.parsed.scopes;
    let decl = scopes[k].decl.as_ref().expect("declaration scope");
    match scopes[k].parent {
        Some(0) if decl.class => decl.name.starts_with("Test"),
        Some(0) => decl.name.starts_with("test"),
        Some(parent) => {
            !decl.class
                && decl.name.starts_with("test")
                && scopes[parent].parent == Some(0)
                && scopes[parent]
                    .decl
                    .as_ref()
                    .is_some_and(|d| d.class && d.name.starts_with("Test"))
        }
        None => false,
    }
}

/// The local name an import binds.
fn bound_name(import: &Import) -> Option<&str> {
    match &import.item {
        ImportItem::Module { alias } => alias
            .as_deref()
            .or(import.module.first().map(String::as_str)),
        ImportItem::Name { name, alias } => Some(alias.as_deref().unwrap_or(name)),
        ImportItem::Star => None,
    }
}

/// The import as written, for unresolved targets: `..pkg.mod.name`.
fn import_text(import: &Import) -> String {
    let mut text = ".".repeat(import.level as usize);
    text.push_str(&import.module.join("."));
    if let ImportItem::Name { name, .. } = &import.item {
        if !import.module.is_empty() {
            text.push('.');
        }
        text.push_str(name);
    }
    text
}

impl Context<'_> {
    fn emit(&self, i: usize, batch: &mut Batch) {
        let file = &self.files[i];
        let scopes = &file.parsed.scopes;
        let evidence = |range: ByteRange| {
            Some(SourceRef {
                file: file.path.clone(),
                range,
                digest: file.digest.clone(),
            })
        };
        let entity = |k: usize| match &file.ids[k] {
            Some(id) => EntityId::Symbol(id.clone()),
            None => EntityId::File(file.path.clone()),
        };
        let edge = |kind, from: EntityId, (to, basis): (Target, Basis), range| Relation {
            kind,
            from,
            to,
            basis,
            evidence: evidence(range),
        };

        let mut bindings: Vec<Vec<(String, Res)>> = Vec::new();
        for (k, scope) in scopes.iter().enumerate() {
            let mut bound = Vec::new();
            for import in &scope.imports {
                let (target, binding) = self.import(i, import);
                let to = target_of(target, &import_text(import), false);
                batch
                    .relations
                    .push(edge(RelationKind::Imports, entity(k), to, import.range));
                if let (Some(name), Some(res)) = (bound_name(import), binding) {
                    bound.push((name.to_owned(), res));
                }
            }
            bindings.push(bound);
        }

        for (k, scope) in scopes.iter().enumerate() {
            if let Some(decl) = &scope.decl {
                let id = entity(k);
                let parent = scope.parent.expect("declaration scope");
                let method = scopes[parent].decl.as_ref().is_some_and(|d| d.class);
                batch.entities.push(Entity {
                    id: id.clone(),
                    kind: if decl.class {
                        "class"
                    } else if method {
                        "method"
                    } else {
                        "function"
                    }
                    .into(),
                    name: decl.name.clone(),
                    signature: Some(decl.signature.clone()),
                    source: evidence(decl.range),
                    test: self.tests.contains(file.ids[k].as_ref().expect("declared")),
                });
                batch.relations.push(contains(entity(parent), id.clone()));
                if let (0, Some(module)) = (parent, &file.module) {
                    batch.relations.push(edge(
                        RelationKind::Defines,
                        module_id(module),
                        (Target::Resolved(id.clone()), Basis::Syntactic),
                        decl.range,
                    ));
                }
                for base in &scope.bases {
                    let res = self.lookup(i, &bindings, parent, &base.name);
                    let to = target_of(res, &base.name, false);
                    batch.relations.push(edge(
                        RelationKind::References,
                        id.clone(),
                        to,
                        base.range,
                    ));
                }
            }
            for call in &scope.calls {
                let res = self.lookup(i, &bindings, k, &call.name);
                let to = target_of(res, &call.name, true);
                if let Target::Resolved(EntityId::Symbol(target)) = &to.0
                    && !self.tests.contains(target)
                    && let Some(test) = self.enclosing_test(i, k)
                {
                    batch.relations.push(edge(
                        RelationKind::TestedBy,
                        EntityId::Symbol(target.clone()),
                        (Target::Resolved(EntityId::Symbol(test)), Basis::Heuristic),
                        call.range,
                    ));
                }
                batch
                    .relations
                    .push(edge(RelationKind::Calls, entity(k), to, call.range));
            }
        }
    }

    fn enclosing_test(&self, i: usize, mut k: usize) -> Option<SymbolId> {
        let file = &self.files[i];
        loop {
            let id = file.ids[k].as_ref()?;
            if self.tests.contains(id) {
                return Some(id.clone());
            }
            k = file.parsed.scopes[k].parent?;
        }
    }

    /// Python name lookup from scope `k`. A dotted name never resolves.
    fn lookup(&self, i: usize, bindings: &[Vec<(String, Res)>], k: usize, name: &str) -> Res {
        let file = &self.files[i];
        if name.contains('.') || file.parsed.rebound.iter().any(|n| n == name) {
            return Res::Opaque;
        }
        let scopes = &file.parsed.scopes;
        let mut current = Some(k);
        while let Some(s) = current {
            let class_body = scopes[s].decl.as_ref().is_some_and(|d| d.class);
            if (s == k || !class_body)
                && let Some(res) = self.in_scope(i, s, name, Some(&bindings[s]))
            {
                return res;
            }
            current = scopes[s].parent;
        }
        Res::Opaque
    }

    /// What `name` means in scope `s`, if `s` binds it. Without `bindings`
    /// (another module's names), any import binding of the name is opaque:
    /// re-exports are not followed.
    fn in_scope(
        &self,
        i: usize,
        s: usize,
        name: &str,
        bindings: Option<&[(String, Res)]>,
    ) -> Option<Res> {
        let file = &self.files[i];
        let scope = &file.parsed.scopes[s];
        let star = scope.imports.iter().any(|m| m.item == ImportItem::Star);
        if star || scope.bound.iter().any(|b| b == name) {
            return Some(Res::Opaque);
        }
        let mut found: Vec<EntityId> = file.children[s]
            .iter()
            .filter(|&&c| file.parsed.scopes[c].decl.as_ref().expect("declared").name == name)
            .map(|&c| EntityId::Symbol(file.ids[c].clone().expect("declared")))
            .collect();
        let mut any = !found.is_empty();
        match bindings {
            Some(bindings) => {
                for (_, res) in bindings.iter().filter(|(n, _)| n == name) {
                    any = true;
                    match res {
                        Res::Found(ids) => found.extend(ids.iter().cloned()),
                        Res::Opaque => return Some(Res::Opaque),
                    }
                }
            }
            None if scope.imports.iter().any(|m| bound_name(m) == Some(name)) => {
                return Some(Res::Opaque);
            }
            None => {}
        }
        found.sort();
        found.dedup();
        any.then_some(Res::Found(found))
    }

    /// The import edge's resolution and the resolution of the name it binds.
    fn import(&self, i: usize, import: &Import) -> (Res, Option<Res>) {
        let base = self.absolute(i, import);
        let module = || base.as_deref().map_or(Res::Opaque, |m| self.module(m));
        match &import.item {
            ImportItem::Module { alias: Some(_) } => (module(), Some(module())),
            ImportItem::Module { alias: None } => {
                let head = import
                    .module
                    .first()
                    .map_or(Res::Opaque, |m| self.module(m));
                (module(), Some(head))
            }
            ImportItem::Name { name, .. } => {
                let res = || base.as_deref().map_or(Res::Opaque, |m| self.from(m, name));
                (res(), Some(res()))
            }
            ImportItem::Star => (module(), None),
        }
    }

    fn module(&self, name: &str) -> Res {
        if self.modules.contains_key(name) {
            Res::Found(vec![module_id(name)])
        } else {
            Res::Opaque
        }
    }

    /// `from module import name`: a submodule, or declarations in the
    /// module's own files.
    fn from(&self, module: &str, name: &str) -> Res {
        let mut found = Vec::new();
        let submodule = format!("{module}.{name}");
        if self.modules.contains_key(&submodule) {
            found.push(module_id(&submodule));
        }
        for &j in self.modules.get(module).map_or(&[][..], Vec::as_slice) {
            if self.files[j].parsed.rebound.iter().any(|n| n == name) {
                return Res::Opaque;
            }
            match self.in_scope(j, 0, name, None) {
                Some(Res::Opaque) => return Res::Opaque,
                Some(Res::Found(ids)) => found.extend(ids),
                None => {}
            }
        }
        found.sort();
        found.dedup();
        if found.is_empty() {
            Res::Opaque
        } else {
            Res::Found(found)
        }
    }

    /// The absolute module an import names, if it has one.
    fn absolute(&self, i: usize, import: &Import) -> Option<String> {
        if import.level == 0 {
            return (!import.module.is_empty()).then(|| import.module.join("."));
        }
        let file = &self.files[i];
        let mut package: Vec<&str> = file.module.as_deref()?.split('.').collect();
        if !file.init {
            package.pop();
        }
        for _ in 1..import.level {
            package.pop()?;
        }
        if package.is_empty() {
            // A relative import needs a parent package.
            return None;
        }
        package.extend(import.module.iter().map(String::as_str));
        Some(package.join("."))
    }
}

/// Maps a resolution onto a relation target. Calls only resolve to symbols.
fn target_of(res: Res, name: &str, symbols_only: bool) -> (Target, Basis) {
    match res {
        Res::Found(mut ids)
            if !ids.is_empty()
                && (!symbols_only || ids.iter().all(|id| matches!(id, EntityId::Symbol(_)))) =>
        {
            if ids.len() == 1 {
                (Target::Resolved(ids.remove(0)), Basis::Resolved)
            } else {
                (Target::Ambiguous(ids), Basis::Syntactic)
            }
        }
        _ => (
            Target::Unresolved {
                name: name.to_owned(),
            },
            Basis::Syntactic,
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_names_follow_the_path_convention() {
        let name = |p: &str| module_name(&RepoPath::new(p).unwrap());
        assert_eq!(name("a/b.py").as_deref(), Some("a.b"));
        assert_eq!(name("a/b/__init__.py").as_deref(), Some("a.b"));
        assert_eq!(name("__init__.py"), None);
        assert_eq!(name("my-tool/x.py"), None);
        assert_eq!(name("a.b/c.py"), None);
        assert_eq!(name("README.md"), None);
    }
}
