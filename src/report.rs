//! Result semantics supplied by the collector, never copied from target records.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// Optional version-1 partition of supported REP MOVS/STOS work. Absence of
/// this object is legacy, unspecified detail, not four observed zero counters.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RepBreakdown {
    version: u64,
    movs_bytes: u64,
    stos_bytes: u64,
    movs_entries: u64,
    stos_entries: u64,
}

impl Default for RepBreakdown {
    fn default() -> Self {
        Self::from_counts(0, 0, 0, 0)
    }
}

impl RepBreakdown {
    pub fn from_counts(
        movs_bytes: u64,
        stos_bytes: u64,
        movs_entries: u64,
        stos_entries: u64,
    ) -> Self {
        Self {
            version: 1,
            movs_bytes,
            stos_bytes,
            movs_entries,
            stos_entries,
        }
    }

    /// Validate the closed aggregate object and exact unsigned integer types.
    pub fn from_value(value: &Value) -> Option<Self> {
        if !value.is_object() {
            return None;
        }
        let parsed: Self = serde_json::from_value(value.clone()).ok()?;
        (parsed.version == 1).then_some(parsed)
    }

    pub fn matches_totals(self, bytes: u64, entries: u64, enabled: bool) -> bool {
        self.version == 1
            && (self.movs_bytes == 0 || self.movs_entries > 0)
            && (self.stos_bytes == 0 || self.stos_entries > 0)
            && self.movs_bytes.checked_add(self.stos_bytes) == Some(bytes)
            && self.movs_entries.checked_add(self.stos_entries) == Some(entries)
            && (enabled || self == Self::default())
    }

    pub fn checked_add(self, other: Self) -> Option<Self> {
        if self.version != 1 || other.version != 1 {
            return None;
        }
        Some(Self {
            version: 1,
            movs_bytes: self.movs_bytes.checked_add(other.movs_bytes)?,
            stos_bytes: self.stos_bytes.checked_add(other.stos_bytes)?,
            movs_entries: self.movs_entries.checked_add(other.movs_entries)?,
            stos_entries: self.stos_entries.checked_add(other.stos_entries)?,
        })
    }
}

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
