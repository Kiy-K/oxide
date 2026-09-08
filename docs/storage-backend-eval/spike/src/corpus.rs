//! Deterministic synthetic corpus: symbol rows shaped like OXIDE's `Symbol`
//! plus a 384-d vector each (arctic-embed-xs dim). Seeded LCG so both
//! backends see byte-identical input.

#[cfg(feature = "surreal")]
use surrealdb::types::SurrealValue;

// SurrealDB 3.x dropped serde for its own `SurrealValue` trait, so a
// domain struct has to carry a database-specific derive to cross the
// boundary at all. Noted as a gate finding, not worked around. Turso needs
// no such derive — it binds plain values — so the derive is feature-gated
// rather than unconditional.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
#[cfg_attr(feature = "surreal", derive(surrealdb::types::SurrealValue))]
pub struct Sym {
    pub id: i64,
    pub file: String,
    pub qualified_name: String,
    pub name: String,
    pub signature: String,
    pub body: String,
    pub start_line: u32,
    pub end_line: u32,
    pub content_hash: i64,
    pub calls: Vec<String>,
    pub bases: Vec<String>,
}

pub struct Rng(u64);
impl Rng {
    pub fn new(seed: u64) -> Self {
        Rng(seed)
    }
    pub fn next(&mut self) -> u64 {
        // xorshift64*
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    pub fn unit(&mut self) -> f32 {
        (self.next() >> 40) as f32 / (1u64 << 24) as f32 - 0.5
    }
}

pub const DIM: usize = 384;

const VERBS: &[&str] = &[
    "resolve", "build", "parse", "emit", "scan", "merge", "fetch", "flush", "expand", "rank",
];
const NOUNS: &[&str] = &[
    "index", "symbol", "vector", "graph", "token", "buffer", "cursor", "segment", "packet",
    "cache",
];

/// `files` files x `per_file` symbols. Deterministic for a given (seed, shape).
pub fn corpus(seed: u64, files: usize, per_file: usize) -> Vec<Sym> {
    let mut r = Rng::new(seed);
    let mut out = Vec::with_capacity(files * per_file);
    let mut id: i64 = 0;
    for f in 0..files {
        let file = format!("pkg{}/mod_{f}.py", f % 40);
        for k in 0..per_file {
            let name = format!(
                "{}_{}_{k}",
                VERBS[r.below(VERBS.len())],
                NOUNS[r.below(NOUNS.len())]
            );
            let qualified_name = format!("mod_{f}.{name}");
            // Bodies carry local identifiers, which is why OXIDE indexes them
            // at weight 1 (AGENTS.md). Keep that texture.
            let body = format!(
                "def {name}(self, {n0}, {n1}):\n    tmp_{k} = {n0} + {n1}\n    return self.{n2}(tmp_{k})\n",
                n0 = NOUNS[r.below(NOUNS.len())],
                n1 = NOUNS[r.below(NOUNS.len())],
                n2 = VERBS[r.below(VERBS.len())],
            );
            let calls = (0..r.below(4))
                .map(|_| format!("{}_{}", VERBS[r.below(VERBS.len())], NOUNS[r.below(NOUNS.len())]))
                .collect();
            let bases = if r.below(5) == 0 {
                vec![format!("Base{}", r.below(20))]
            } else {
                vec![]
            };
            out.push(Sym {
                id,
                file: file.clone(),
                qualified_name,
                name,
                signature: format!("def name(self, a, b) -> {}", NOUNS[r.below(NOUNS.len())]),
                body,
                start_line: (k * 8 + 1) as u32,
                end_line: (k * 8 + 6) as u32,
                content_hash: r.next() as i64,
                calls,
                bases,
            });
            id += 1;
        }
    }
    out
}

/// One L2-normalized vector per symbol, deterministic in the symbol id.
pub fn vectors(syms: &[Sym]) -> Vec<Vec<f32>> {
    syms.iter()
        .map(|s| {
            let mut r = Rng::new(0xBEEF ^ s.id as u64);
            let mut v: Vec<f32> = (0..DIM).map(|_| r.unit()).collect();
            let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
            v.iter_mut().for_each(|x| *x /= n);
            v
        })
        .collect()
}

/// Ground truth for a **token** index: does `body` contain `term` as a whole
/// word — a maximal `[A-Za-z0-9_]` run — rather than as a raw substring?
///
/// Using raw `contains` here is a real bug, and multi-probing found it: with
/// bodies holding `tmp_0` .. `tmp_24`, `body.contains("tmp_1")` is also true
/// for `tmp_10` .. `tmp_19`, so the "ground truth" demanded 11x the documents
/// any correct full-text index would return. Both engines were right and the
/// harness was wrong. Word-run equality is engine-neutral: it is what FTS5's
/// `unicode61` and SurrealDB's `class` tokenizer both resolve this term to.
///
/// Literal substring search (G4c) deliberately still uses raw `contains` —
/// there, substring *is* the contract.
pub fn contains_token(body: &str, term: &str) -> bool {
    body.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .any(|w| w == term)
}

pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

/// Probe sets for the correctness gates. One probe proves one query worked;
/// these spread the gates over several distinct targets/terms so a single
/// lucky case cannot carry a PASS.
pub struct Probes {
    pub calls: Vec<String>,
    pub bases: Vec<String>,
    pub terms: Vec<String>,
    pub literals: Vec<String>,
}

/// Picked from the corpus itself so every probe is guaranteed to have a
/// non-empty ground-truth set, and sorted so the choice is deterministic.
pub fn probes(syms: &[Sym], n: usize) -> Probes {
    let mut calls: Vec<String> = syms.iter().flat_map(|s| s.calls.clone()).collect();
    calls.sort();
    calls.dedup();
    let mut bases: Vec<String> = syms.iter().flat_map(|s| s.bases.clone()).collect();
    bases.sort();
    bases.dedup();
    Probes {
        calls: calls.into_iter().take(n).collect(),
        bases: bases.into_iter().take(n).collect(),
        // `tmp_K` appears once per file at position K, so each term has a
        // large, exactly-known truth set.
        terms: (0..n).map(|k| format!("tmp_{k}")).collect(),
        literals: (0..n).map(|k| format!("tmp_{k} = ")).collect(),
    }
}

/// FNV-1a over the new body. An edit that changes `embed_text` must change the
/// symbol's `content_hash`, or the embedding cache would wrongly reuse the old
/// vector (`AGENTS.md`: the cache-invalidation key must equal a hash of
/// `embed_text(symbol)`, never a proxy).
pub fn body_hash(body: &str) -> i64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in body.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h as i64
}

/// One symbol's vector, regenerated from its (new) content hash so an edited
/// symbol gets a genuinely different embedding.
pub fn vector_for(hash: i64) -> Vec<f32> {
    let mut r = Rng::new(0xBEEF ^ hash as u64);
    let mut v: Vec<f32> = (0..DIM).map(|_| r.unit()).collect();
    let n: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    v.iter_mut().for_each(|x| *x /= n);
    v
}
