//! Decodes the kernel's five flat buffers (layout: vendor/.../src/buffers.rs,
//! ABI v2) into plain structs. This is the work every consumer of the kernel
//! must do, so the bench times it separately from the extraction itself.

use crate::buffers::{EmitOut, EDGE_ROW_SIZE, META_SIZE, NODE_ROW_SIZE, NONE, REF_ROW_SIZE};

#[derive(Debug, Clone)]
pub struct Node {
    pub kind: &'static str,
    pub start_line: u32,
    pub end_line: u32,
    pub start_col: u32,
    pub end_col: u32,
    pub name: String,
    pub qualified_name: Option<String>,
    pub id: Option<String>,
    pub signature: Option<String>,
    pub exported: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct Edge {
    pub source: Endpoint,
    pub target: Endpoint,
    pub kind: &'static str,
    pub line: Option<u32>,
}

#[derive(Debug, Clone)]
pub enum Endpoint {
    Row(u32),
    Id(String),
}

#[derive(Debug, Clone)]
pub struct Ref {
    pub from: Endpoint,
    pub kind: &'static str,
    pub line: u32,
    pub column: u32,
    pub name: String,
    pub candidates: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct Decoded {
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
    pub refs: Vec<Ref>,
    pub errors_json: Option<String>,
}

fn u32_at(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(b[o..o + 4].try_into().unwrap())
}

fn str_at(arena: &[u8], row: &[u8], o: usize) -> Option<String> {
    let off = u32_at(row, o);
    if off == NONE {
        return None;
    }
    let len = u32_at(row, o + 4) as usize;
    let off = off as usize;
    Some(String::from_utf8_lossy(&arena[off..off + len]).into_owned())
}

fn endpoint(row: &[u8], idx_off: usize, arena: &[u8], id_off: usize) -> Endpoint {
    let idx = u32_at(row, idx_off);
    if idx != NONE {
        Endpoint::Row(idx)
    } else {
        Endpoint::Id(str_at(arena, row, id_off).unwrap_or_default())
    }
}

pub fn decode(out: &EmitOut) -> Decoded {
    let m = &out.meta;
    assert_eq!(m.len(), META_SIZE);
    let (nn, ne, nr) = (u32_at(m, 4), u32_at(m, 8), u32_at(m, 12));
    let arena = &out.arena;
    let errors_json = {
        let off = u32_at(m, 20);
        (off != NONE).then(|| {
            let len = u32_at(m, 24) as usize;
            String::from_utf8_lossy(&arena[off as usize..off as usize + len]).into_owned()
        })
    };
    let nodes = out
        .nodes
        .chunks_exact(NODE_ROW_SIZE)
        .take(nn as usize)
        .map(|r| {
            let flags = u16::from_le_bytes([r[2], r[3]]);
            Node {
                kind: crate::node_kind_name(r[0]),
                start_line: u32_at(r, 4),
                end_line: u32_at(r, 8),
                start_col: u32_at(r, 12),
                end_col: u32_at(r, 16),
                name: str_at(arena, r, 20).unwrap_or_default(),
                qualified_name: str_at(arena, r, 28),
                id: str_at(arena, r, 36),
                signature: str_at(arena, r, 52),
                exported: (flags & 1 != 0).then_some(flags & 2 != 0),
            }
        })
        .collect();
    let edges = out
        .edges
        .chunks_exact(EDGE_ROW_SIZE)
        .take(ne as usize)
        .map(|r| {
            let line = u32_at(r, 12);
            Edge {
                source: endpoint(r, 0, arena, 28),
                target: endpoint(r, 4, arena, 36),
                kind: crate::edge_kind_name(r[8]),
                line: (line != NONE).then_some(line),
            }
        })
        .collect();
    let refs = out
        .refs
        .chunks_exact(REF_ROW_SIZE)
        .take(nr as usize)
        .map(|r| Ref {
            from: endpoint(r, 0, arena, 32),
            kind: crate::edge_kind_name(r[4]),
            line: u32_at(r, 8),
            column: u32_at(r, 12),
            name: str_at(arena, r, 16).unwrap_or_default(),
            candidates: str_at(arena, r, 24)
                .map(|s| s.split('\0').map(str::to_string).collect())
                .unwrap_or_default(),
        })
        .collect();
    Decoded {
        nodes,
        edges,
        refs,
        errors_json,
    }
}
