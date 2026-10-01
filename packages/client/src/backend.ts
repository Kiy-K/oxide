// The seam between the public `Oxide` API and how a request reaches the Rust
// binary. Internal: only `ProcessBackend` exists today; a future in-process
// backend would implement the same interface and leave `Oxide` unchanged.

export type Profile = "fast" | "balanced" | "quality";

export interface SearchOptions {
  /** Maximum hits (`--limit`); the binary's default is 10. */
  limit?: number;
  /** Ranking channels (`--mode`); literal search is `Oxide.searchLiteral`. */
  mode?: "lexical" | "semantic" | "hybrid";
  profile?: Profile;
  /** `false` disables structural expansion (`--no-expand`). */
  expand?: boolean;
  /** Attach each seed hit's bounded impact neighborhood (`--blast-radius`). */
  blastRadius?: boolean;
}

export interface LiteralOptions {
  /** Maximum hits (`--limit`); the binary's default is 10. */
  limit?: number;
}

export interface QueryOptions {
  /** Token budget for the pack (`--budget-tokens`). */
  budgetTokens?: number;
  profile?: Profile;
  blastRadius?: boolean;
  /**
   * Add the current diff's evidence (`--git`). The result's extra `git`
   * object is not modeled by `@oxide/protocol` and passes through as-is.
   */
  git?: boolean;
}

export interface IndexOptions {
  /** Rebuild everything, even unchanged files (`--rebuild`). */
  rebuild?: boolean;
}

/**
 * The raw JSON the binary answered with. `ok: false` carries what should be
 * an OXIDE error envelope; the client validates both sides.
 */
export type Outcome = { ok: true; json: unknown } | { ok: false; json: unknown };

export interface Backend {
  status(): Promise<Outcome>;
  index(options: IndexOptions): Promise<Outcome>;
  search(query: string, options: SearchOptions): Promise<Outcome>;
  searchLiteral(pattern: string, options: LiteralOptions): Promise<Outcome>;
  query(task: string, options: QueryOptions): Promise<Outcome>;
}
