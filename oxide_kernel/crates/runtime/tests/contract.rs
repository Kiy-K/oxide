//! The runtime answers every shared contract case (contract/cases.json) with
//! exactly the recorded response. The TS side replays the same cases through
//! the real binary (src/transport.test.ts).

use serde_json::Value;

#[test]
fn runtime_matches_contract_cases() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("../../../contract/cases.json")).unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let request = case["request"].as_str().unwrap();
        assert_eq!(
            oxide_runtime::handle(request),
            case["response"],
            "case {}",
            case["name"]
        );
    }
}
