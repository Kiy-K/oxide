// The seam between the public `Oxide` API and how a request reaches the Rust
// binary. Internal: only `ProcessBackend` exists today; a future in-process
// backend would implement the same interface and leave `Oxide` unchanged.

export type Profile = "fast" | "balanced" | "quality";

export interface SearchOptions {
  /** Maximum hits (`--limit`); the binary's default is 10. */
  limit?: number;
  /** Ranking channels (`--mode`); literal search has another shape and is not offered. */
  mode?: "lexical" | "semantic" | "hybrid";
  profile?: Profile;
  /** `false` disables structural expansion (`--no-expand`). */
  expand?: boolean;
  /** Attach each seed hit's bounded impact neighborhood (`--blast-radius`). */
  blastRadius?: boolean;
}

export interface QueryOptions {
  /** Token budget for the pack (`--budget-tokens`). */
  budgetTokens?: number;
  profile?: Profile;
  blastRadius?: boolean;
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
  query(task: string, options: QueryOptions): Promise<Outcome>;
}
