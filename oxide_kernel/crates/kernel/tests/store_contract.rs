//! KnowledgeStore contract suite (SPEC § Storage abstraction). Every case is
//! generic over the store; `contract_suite!` instantiates it per
//! implementation, so the Phase 2 LadybugDB adapter runs the same cases.

mod common;

macro_rules! contract_suite {
    ($store:ident: $make:expr; $($case:ident),* $(,)?) => {
        mod $store {
            $( #[test] fn $case() { super::$case($make) } )*
        }
    };
}

contract_suite!(memory: oxide_kernel::store::MemoryStore::default();
    unpublished_generation_is_invisible,
    failed_publication_stays_invisible,
    derivations_are_never_mixed,
    lookup_is_scoped_ordered_and_explicit_about_missing,
    adjacency_is_typed_bounded_and_stable,
    views_stay_pinned_across_publication,
    published_generations_are_immutable,
    publication_is_independent_of_write_order,
    superseded_generations_are_freed_when_unpinned,
    lexical_search_is_scoped_ordered_and_bounded,
);

use common::store_cases::*;
