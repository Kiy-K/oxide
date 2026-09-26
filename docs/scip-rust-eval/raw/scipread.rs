//! Decode a SCIP index with the `scip` crate and flatten it to JSON for the
//! comparison harness. Timing of the decode alone goes to stderr so
//! consumption cost can be separated from generation cost.
//!
//! Output: {"documents":[{"path","occ":[[symbol, sl, sc, el, ec, is_def,
//! [enclosing sl, el] | null]]}], "symbols":{symbol: [kind, display_name]}}
//! Lines are converted to 1-based to match OXIDE.
use std::time::Instant;

use protobuf::Message;
use scip::types::{symbol_information::Kind, Index, SymbolRole};
use serde_json::{json, Map, Value};

fn lines(r: &[i32]) -> (i32, i32, i32, i32) {
    match r.len() {
        3 => (r[0] + 1, r[1], r[0] + 1, r[2]),
        4 => (r[0] + 1, r[1], r[2] + 1, r[3]),
        _ => (0, 0, 0, 0),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let bytes = std::fs::read(&args[1]).expect("read index");
    let t = Instant::now();
    let index = Index::parse_from_bytes(&bytes).expect("decode");
    let decode = t.elapsed();

    let mut n_occ = 0usize;
    let mut n_local = 0usize;
    let mut symbols = Map::new();
    let mut docs = Vec::new();
    for d in &index.documents {
        for s in &d.symbols {
            let kind = s.kind.enum_value().unwrap_or(Kind::UnspecifiedKind);
            symbols.insert(
                s.symbol.clone(),
                json!([format!("{kind:?}"), s.display_name]),
            );
        }
        let mut occ = Vec::with_capacity(d.occurrences.len());
        for o in &d.occurrences {
            n_occ += 1;
            if o.symbol.starts_with("local ") {
                n_local += 1;
                continue;
            }
            let (sl, sc, el, ec) = lines(&o.range);
            let is_def = o.symbol_roles & SymbolRole::Definition as i32 != 0;
            let encl = if o.enclosing_range.is_empty() {
                Value::Null
            } else {
                let (a, _, b, _) = lines(&o.enclosing_range);
                json!([a, b])
            };
            occ.push(json!([o.symbol, sl, sc, el, ec, is_def, encl]));
        }
        docs.push(json!({"path": d.relative_path, "occ": occ}));
    }
    for s in &index.external_symbols {
        let kind = s.kind.enum_value().unwrap_or(Kind::UnspecifiedKind);
        symbols
            .entry(s.symbol.clone())
            .or_insert(json!([format!("{kind:?}"), s.display_name]));
    }
    let flatten = t.elapsed() - decode;
    eprintln!(
        "{}",
        json!({"bytes": bytes.len(), "decode_ms": decode.as_secs_f64() * 1e3,
               "flatten_ms": flatten.as_secs_f64() * 1e3,
               "documents": index.documents.len(), "occurrences": n_occ,
               "local_occurrences": n_local, "symbols": symbols.len(),
               "external_symbols": index.external_symbols.len()})
    );
    if args.get(2).map(String::as_str) != Some("--stats-only") {
        println!("{}", json!({"documents": docs, "symbols": symbols}));
    }
}
