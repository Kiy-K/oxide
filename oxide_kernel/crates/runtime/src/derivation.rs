//! Canonical versioned component identity, distinct from captured source.
use oxide_kernel::id::DerivationId;
use std::collections::BTreeMap;

/// Components sort by byte-exact key. Every string is length-prefixed with
/// its UTF-8 byte length as little-endian u64; count is little-endian u64.
pub fn derivation_id(components: &BTreeMap<String, String>) -> DerivationId {
    let mut bytes = Vec::new();
    fn field(bytes: &mut Vec<u8>, value: &str) {
        bytes.extend_from_slice(&(value.len() as u64).to_le_bytes());
        bytes.extend_from_slice(value.as_bytes());
    }
    field(&mut bytes, "oxide-derivation-v1");
    bytes.extend_from_slice(&(components.len() as u64).to_le_bytes());
    for (key, value) in components {
        field(&mut bytes, key);
        field(&mut bytes, value);
    }
    DerivationId::new(crate::capture::digest(&bytes).as_str()).expect("SHA-256 is a valid ID")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn components_are_order_independent_but_changes_are_isolated() {
        let a = BTreeMap::from([
            ("parser".into(), "1".into()),
            ("storage".into(), "47".into()),
        ]);
        let b = [
            ("storage".into(), "47".into()),
            ("parser".into(), "1".into()),
        ]
        .into_iter()
        .collect();
        assert_eq!(derivation_id(&a), derivation_id(&b));
        let mut changed = a.clone();
        changed.insert("parser".into(), "2".into());
        assert_ne!(derivation_id(&a), derivation_id(&changed));
        assert_ne!(
            derivation_id(&BTreeMap::from([("a".into(), "bc".into())])),
            derivation_id(&BTreeMap::from([("ab".into(), "c".into())]))
        );
    }
}
