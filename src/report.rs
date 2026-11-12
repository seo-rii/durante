//! Result semantics supplied by the collector, never copied from target records.
use serde_json::{json, Value};

/// A versioned description, not an attestation or a claim of CPU-time fairness.
pub fn measurement_contract() -> Value {
    json!({
        "version": 1,
        "backend": "dynamorio",
        "counter_integrity": "application_mutable",
        "trusted_verdict_eligible": false,
        "instruction_semantics": "basic_block_entry",
        "simd_weighting": "one_per_instruction",
        "cpu_time_conversion": null
    })
}

/// Only call after validating the complete record tree and its counter sums.
pub fn rep_telemetry_status(enabled: bool, unsupported_entries: u64) -> &'static str {
    if !enabled {
        "disabled"
    } else if unsupported_entries > 0 {
        "partial"
    } else {
        "movs_stos_only"
    }
}
