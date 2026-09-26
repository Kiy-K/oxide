//! Bounded ranking over `(id, score)` pairs: the one total order every
//! ranked retrieval surface uses ([`cmp_score_id`]), a streaming top-K
//! heap for the vector scan ([`TopK`]) and a select-then-sort top-K for
//! an already-materialized list ([`top_k_by_score`]).

/// Score-descending order with a stable tie-break on symbol id. `HashMap`
/// iteration order is randomized per process, so without this, results tied
/// on score (a common outcome of the discrete RRF/BM25 formulas) would sort
/// differently across otherwise-identical runs — read-only search/context
/// must be deterministic for the same index and query.
pub(super) fn cmp_score_id(a: &(u64, f32), b: &(u64, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

/// Bounded top-K under [`cmp_score_id`]: a max-heap whose top is the
/// *worst* retained entry, so admission is one comparison and the result
/// is exactly the first K of a full sort — the comparator is a total
/// order over distinct ids, so the retained set and its order are the
/// same either way.
pub(super) struct TopK {
    k: usize,
    heap: std::collections::BinaryHeap<Worst>,
}

struct Worst(u64, f32);

impl PartialEq for Worst {
    fn eq(&self, other: &Self) -> bool {
        self.0 == other.0
    }
}
impl Eq for Worst {}
impl PartialOrd for Worst {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Worst {
    /// `cmp_score_id` sorts best-first, so "greater" already means "sorts
    /// later, i.e. worse": the heap's maximum is the eviction candidate.
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        cmp_score_id(&(self.0, self.1), &(other.0, other.1))
    }
}

impl TopK {
    pub(super) fn new(k: usize) -> Self {
        Self {
            k,
            heap: std::collections::BinaryHeap::with_capacity(k + 1),
        }
    }

    pub(super) fn push(&mut self, id: u64, score: f32) {
        if self.k == 0 {
            return;
        }
        if self.heap.len() < self.k {
            self.heap.push(Worst(id, score));
            return;
        }
        let worst = self.heap.peek().map(|w| (w.0, w.1)).unwrap_or((0, 0.0));
        if cmp_score_id(&(id, score), &worst) == std::cmp::Ordering::Less {
            self.heap.pop();
            self.heap.push(Worst(id, score));
        }
    }

    /// Retained entries, best first.
    pub(super) fn into_sorted(self) -> Vec<(u64, f32)> {
        let mut out: Vec<(u64, f32)> = self.heap.into_iter().map(|w| (w.0, w.1)).collect();
        out.sort_by(cmp_score_id);
        out
    }
}

/// The first `k` of `sort_by(cmp_score_id)`, without sorting the rest.
pub(super) fn top_k_by_score(mut items: Vec<(u64, f32)>, k: usize) -> Vec<(u64, f32)> {
    if items.len() > k {
        if k == 0 {
            return Vec::new();
        }
        items.select_nth_unstable_by(k - 1, cmp_score_id);
        items.truncate(k);
    }
    items.sort_by(cmp_score_id);
    items
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::retrieval::test_support::lcg;

    /// The bounded heap must retain exactly the first K of a full sort —
    /// same set, same order — including under heavy score ties, where the
    /// id tie-break is what decides membership at the K boundary.
    #[test]
    fn bounded_top_k_equals_full_sort_then_take() {
        let mut seed = 7u64;
        for &n in &[0usize, 1, 5, 199, 200, 201, 1000, 5000] {
            // Coarse scores force many exact ties.
            let items: Vec<(u64, f32)> = (0..n)
                .map(|_| (lcg(&mut seed), (lcg(&mut seed) % 7) as f32 / 3.0))
                .collect();
            for &k in &[0usize, 1, 25, 50, 100, 200, 500] {
                let mut expected = items.clone();
                expected.sort_by(cmp_score_id);
                expected.truncate(k);
                let mut heap = TopK::new(k);
                for &(id, score) in &items {
                    heap.push(id, score);
                }
                assert_eq!(heap.into_sorted(), expected, "n={n} k={k} (heap)");
                assert_eq!(
                    top_k_by_score(items.clone(), k),
                    expected,
                    "n={n} k={k} (select)"
                );
            }
        }
    }
}
