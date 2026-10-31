# Rust edge spot checks

Checked the first 10 resolved cross-file call rows and first 10 resolved import
rows in each current audit JSON against the pinned source checkout: 40 edges in
total. Each cited expression/import agrees with the target declaration or
signature. No contradictions found. This bounded inspection is not a precision
estimate.

## ripgrep

| Kind | Expression | Target | Correct | Why |
|---|---|---|---|---|
| Call | `ignore::types::TypesBuilder::new` | `crates/ignore/src/types.rs#<impl TypesBuilder>.new` | yes | Qualified constructor resolves to the inherent constructor. |
| Call | `SearcherBuilder::new` | `crates/searcher/src/searcher/mod.rs#<impl SearcherBuilder>.new` | yes | Builder constructor matches the target impl and signature. |
| Call/reference | `Printer::Standard` | `crates/core/search.rs#Printer` | yes | Enum variant expression names the containing enum. |
| Call | `parse_low_raw` | `crates/core/flags/parse.rs#parse_low_raw` | yes | Imported helper name and target signature match. |
| Macro | `rgtest` | `tests/macros.rs#rgtest` | yes | Test macro invocation resolves to its `macro_rules!` definition. |
| Macro | `rgtest` | `tests/macros.rs#rgtest` | yes | Test macro invocation resolves to its `macro_rules!` definition. |
| Macro | `rgtest` | `tests/macros.rs#rgtest` | yes | Test macro invocation resolves to its `macro_rules!` definition. |
| Call | `IncrementalIgnore::new` | `crates/ignore/src/incremental.rs#<impl IncrementalIgnore>.new` | yes | Qualified constructor and argument-bearing signature agree. |
| Call | `RegexMatcher::new` | `crates/regex/src/matcher.rs#<impl RegexMatcher>.new` | yes | Qualified constructor resolves to the matcher constructor. |
| Call | `grep::printer::HyperlinkConfig::new` | `crates/printer/src/hyperlink/mod.rs#<impl HyperlinkConfig>.new` | yes | Fully qualified constructor matches the target impl. |
| Import | `GlobSetBuilder` | `crates/globset/src/lib.rs#GlobSetBuilder` | yes | Imported type matches the struct declaration. |
| Import | `grep_regex::RegexMatcherBuilder` | `crates/regex/src/matcher.rs#RegexMatcherBuilder` | yes | External crate alias and imported builder declaration agree. |
| Import | `JSONSink` | `crates/printer/src/json.rs#JSONSink` | yes | Re-export names the declared struct. |
| Import | `Candidate` | `crates/globset/src/lib.rs#Candidate` | yes | Imported type matches the declaration. |
| Import | `ignore::gitignore::GitignoreBuilder` | `crates/ignore/src/gitignore.rs#GitignoreBuilder` | yes | Qualified import resolves to the declared builder. |
| Import | `Match` | `crates/ignore/src/lib.rs#Match` | yes | Imported enum is the matching crate-level declaration. |
| Import | `WalkParallel` | `crates/ignore/src/walk.rs#WalkParallel` | yes | Imported type matches the declaration. |
| Import | `GlobSet` | `crates/globset/src/lib.rs#GlobSet` | yes | Imported type matches the declaration. |
| Import | `escape` | `crates/cli/src/escape.rs#escape` | yes | Imported function name and signature match. |
| Import | `SpecialMode` | `crates/core/flags/lowargs.rs#SpecialMode` | yes | Imported enum matches the target declaration. |

Counts: 10/10 calls correct; 10/10 imports correct; 0 contradictions.

## tokio

| Kind | Expression | Target | Correct | Why |
|---|---|---|---|---|
| Call | `Semaphore::new` | `tokio/src/sync/semaphore.rs#<impl Semaphore>.new` | yes | Constructor expression and signature agree. |
| Call | `runtime::Builder::new_current_thread` | `tokio/src/runtime/builder.rs#<impl Builder>.new_current_thread` | yes | Qualified builder constructor matches declaration. |
| Call | `UringContext::new` | `tokio/src/runtime/io/driver/uring.rs#<impl UringContext>.new` | yes | Qualified constructor matches the target impl. |
| Call | `Semaphore::new` | `tokio/src/sync/semaphore.rs#<impl Semaphore>.new` | yes | Constructor expression and signature agree. |
| Call | `broadcast::channel` | `tokio/src/sync/broadcast.rs#channel` | yes | Module-qualified function matches the channel declaration. |
| Call | `RwLock::new` | `tokio/src/sync/rwlock.rs#<impl RwLock<T>>.new` | yes | Constructor and value parameter match. |
| Call | `Blocking::new_mandatory` | `tokio/src/io/blocking.rs#<impl Blocking<T>>.new_mandatory` | yes | Qualified associated function matches its declaration. |
| Call | `Buf::with_capacity` | `tokio/src/io/blocking.rs#<impl Buf>.with_capacity` | yes | Associated function and capacity parameter match. |
| Call | `Instant::now` | `tokio/src/time/instant.rs#<impl Instant>.now` | yes | Qualified associated function matches. |
| Call | `MetricsBatch::new` | `tokio/src/runtime/metrics/batch.rs#<impl MetricsBatch>.new` | yes | Constructor expression and worker-metrics parameter match. |
| Import | `AsyncRead` | `tokio/src/io/async_read.rs#AsyncRead` | yes | Imported trait matches declaration. |
| Import | `self::dir_builder::DirBuilder` | `tokio/src/fs/dir_builder.rs#DirBuilder` | yes | Relative module import matches the struct. |
| Import | `tokio::sync` | module `tokio/src/lib.rs::sync` | yes | Import names the declared `sync` module. |
| Import | `crate::fs::asyncify` | `tokio/src/fs/mod.rs#asyncify` | yes | Crate-relative import matches function declaration. |
| Import | `super::*` | module `tokio/tests/task_join_set.rs::spawn_local_on` | yes | Ancestor glob exposes the parent test-module items, including the named item. |
| Import | `crate::io::AsyncBufRead` | `tokio/src/io/async_buf_read.rs#AsyncBufRead` | yes | Crate-relative path resolves to the trait declaration. |
| Import | `AutoBox` | `tokio/src/runtime/mod.rs#AutoBox` | yes | Imported type matches declaration. |
| Import | `self::read_link::read_link` | `tokio/src/fs/read_link.rs#read_link` | yes | Relative module import resolves to the function. |
| Import | `Core` | `tokio/src/runtime/task/core.rs#Core` | yes | Imported type matches declaration. |
| Import | `LocalSet` | `tokio/src/task/local.rs#LocalSet` | yes | Imported type matches declaration. |

Counts: 10/10 calls correct; 10/10 imports correct; 0 contradictions. Every
contradiction: none.
