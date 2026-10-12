// OXIDE v2 control plane (Bun). Integrations, orchestration and presentation
// live here; repository intelligence stays in Rust (oxide_kernel/).
// This entry exports only the public API (docs/public-api.md); the wire
// contract and transport are internal and change without notice.

export {
  API_VERSION,
  type CallOptions,
  type ClientOptions,
  type ContextBudget,
  type ContextBundle,
  type ContextItem,
  type ContextRequest,
  createClient,
  type Diagnostic,
  type ErrorCategory,
  type IndexResult,
  type Operation,
  type OxideClient,
  OxideError,
  type RepositoryHandle,
  type SearchHit,
  type SearchRequest,
  type SearchResult,
  type SourceLocation,
  type Status,
} from "./client.ts";
