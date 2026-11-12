use serde_json::Value;

/// Independent output invariants: rejected input must never leak a usable
/// partial total; accepted output must sum exactly at both aggregation levels.
pub fn assert_outcome(result: &Value) {
    assert_eq!(result["schema"], 1);
    assert_eq!(result["authority"], "diagnostic_only");
    assert_eq!(result["integrity_verified"], false);
    assert_eq!(
        result["measurement_contract"]["trusted_verdict_eligible"],
        false
    );
    assert_eq!(
        result["measurement_contract"]["counter_integrity"],
        "application_mutable"
    );
    if result["valid"] != true {
        assert_eq!(result["rep_telemetry_status"], "unavailable");
        assert_eq!(result["complete"], false);
        for key in [
            "instructions",
            "rep_bytes",
            "rep_entries",
            "unsupported_rep_entries",
            "signals_observed",
            "processes",
            "segments",
        ] {
            assert!(result.get(key).is_none(), "invalid input leaked {key}");
        }
        return;
    }
    assert_eq!(result["complete"], true);
    let expected_telemetry = if result["rep_bytes_enabled"] == false {
        "disabled"
    } else if result["unsupported_rep_entries"].as_u64().unwrap() > 0 {
        "partial"
    } else {
        "movs_stos_only"
    };
    assert_eq!(result["rep_telemetry_status"], expected_telemetry);
    let processes = result["processes"].as_array().unwrap();
    let segments = result["segments"].as_array().unwrap();
    assert!(!processes.is_empty() && processes.len() <= durante::collect::MAX_PROCESSES);
    assert_eq!(result["process_count"], processes.len());
    assert_eq!(result["image_count"], segments.len());
    for key in [
        "instructions",
        "rep_bytes",
        "rep_entries",
        "unsupported_rep_entries",
        "signals_observed",
    ] {
        let total = result[key].as_u64().unwrap() as u128;
        assert_eq!(
            total,
            processes
                .iter()
                .map(|p| p[key].as_u64().unwrap() as u128)
                .sum::<u128>()
        );
        assert_eq!(
            total,
            segments
                .iter()
                .map(|p| p[key].as_u64().unwrap() as u128)
                .sum::<u128>()
        );
        for process in processes {
            assert_eq!(
                process[key].as_u64().unwrap() as u128,
                segments
                    .iter()
                    .filter(|s| s["pid"] == process["pid"])
                    .map(|s| s[key].as_u64().unwrap() as u128)
                    .sum::<u128>()
            );
        }
    }
    assert!(result["instructions"].as_u64().unwrap() > 0);
    if result["rep_bytes_enabled"] == false {
        for key in ["rep_bytes", "rep_entries", "unsupported_rep_entries"] {
            assert_eq!(result[key], 0);
        }
    }
}
