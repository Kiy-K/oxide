//! Deterministic per-repository dump of the **embedding input corpus**: for
//! every symbol in an existing index, its identity, span, stored
//! `content_hash`, and the exact `index::embed_text` string that an embedding
//! of it would be computed from.
//!
//! Why this exists (Phase 2 of the CPU embedding survey,
//! `docs/cpu-embedding-survey/`): the survey compares two embedding
//! providers on the same pinned task set by indexing each worktree twice,
//! once per provider, in sequence. `embed_text` includes `Symbol::references`,
//! and cross-file reference resolution is known to be able to lag by a run
//! (`AGENTS.md`, "Cross-file, same-run reference staleness"), so a repeated
//! `oxide index` can legitimately change some symbols' `content_hash` and
//! therefore what gets embedded. If that happened *between* the two
//! providers' runs, the two would be scored against different corpora and
//! the paired comparison would silently confound model quality with indexing
//! order. This dump makes that falsifiable: capture one manifest per
//! provider, require them byte-identical, and the corpus is proven constant
//! rather than assumed to be.
//!
//! Read-only in the sense this repo defines it (`AGENTS.md`, and
//! `tests/cli_e2e.rs::read_only_commands_never_modify_index_db_content`):
//! `SqliteStore::open_read_only` never modifies `index.db`'s content, but
//! like any WAL reader it may create or touch the writer's `-wal`/`-shm`
//! sidecar files. What matters for this tool's purpose is the stronger
//! guarantee it does hold: it reads what indexing already produced and
//! never re-derives, reparses, or re-resolves anything, so running it
//! cannot change any symbol, span, hash, or `embed_text` it reports.
//!
//! Output is one TSV line per symbol. The **fully rendered lines** are what
//! gets sorted, not a `(file, qualified_name)` key: sorting by that key is
//! only a total order as long as the key is unique, and if it ever were not
//! (a symbol-id hash collision, or a regression in `parse_file`'s duplicate
//! dedup), a stable sort would silently fall back to `all_symbols()`' SQLite
//! row order — reintroducing exactly the nondeterminism this tool exists to
//! rule out. Ordering byte-identical lines can have no such tie. The
//! `embed_text` column is JSON-encoded, so a multi-line signature can never
//! split one symbol across two lines.
//!
//! Usage:
//!   corpus_manifest <repo_dir> [--no-text]   # --no-text omits the last column

use anyhow::Context;
use oxide::embeddings::symbol_embed_text;
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::content_hash;
use std::io::Write;

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let repo = args
        .next()
        .ok_or_else(|| anyhow::anyhow!("usage: corpus_manifest <repo_dir> [--no-text]"))?;
    let no_text = args.any(|a| a == "--no-text");

    // Canonicalized before it is emitted: the header path is provenance, but
    // an un-normalized one makes the manifest depend on how the caller spelled
    // the directory (relative vs absolute, a symlink, a trailing component),
    // which would break the byte-identical comparison this file exists to
    // support between two runs that indexed the very same corpus.
    let repo = std::path::Path::new(&repo)
        .canonicalize()
        .with_context(|| format!("resolve repo path {repo}"))?;
    let db = repo.join(".oxide").join("index.db");
    anyhow::ensure!(db.exists(), "no index at {}", db.display());
    let store = SqliteStore::open_read_only(&db)?;

    let symbols = store.all_symbols()?;
    let mut lines: Vec<String> = Vec::with_capacity(symbols.len());
    for s in &symbols {
        let text = symbol_embed_text(s);
        // The invariant worth pinning alongside identity: an embedding's
        // cache key must equal a hash of exactly this string (AGENTS.md).
        // Emitting both columns lets a diff show whether a mismatch is in
        // the stored hash, the text, or both — they are not redundant: only
        // the module fallback symbol's `content_hash` is itself a hash of
        // `symbol_embed_text`, so every other symbol's `references` can change
        // (changing what is embedded) while `content_hash` stays put.
        let mut line = format!(
            "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            s.id(),
            s.file,
            s.start_line,
            s.end_line,
            s.kind,
            s.qualified_name,
            s.content_hash,
            content_hash(&text)
        );
        if !no_text {
            line.push('\t');
            line.push_str(&serde_json::to_string(&text)?);
        }
        lines.push(line);
    }
    lines.sort();

    let stdout = std::io::stdout();
    let mut out = std::io::BufWriter::new(stdout.lock());
    // The header declares exactly the columns emitted: under `--no-text`
    // the last one is absent, and a header that still advertised it would
    // misdescribe the very file this tool exists to have read literally.
    let columns = if no_text {
        "symbol_id\tfile\tstart_line\tend_line\tkind\tqualified_name\tcontent_hash\tembed_text_hash"
    } else {
        "symbol_id\tfile\tstart_line\tend_line\tkind\tqualified_name\tcontent_hash\tembed_text_hash\tembed_text"
    };
    writeln!(
        out,
        "# repo\t{}\n# symbols\t{}\n# columns\t{}",
        repo.display(),
        lines.len(),
        columns
    )?;
    for line in &lines {
        writeln!(out, "{line}")?;
    }
    out.flush()?;
    Ok(())
}
