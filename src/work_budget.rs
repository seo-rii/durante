//! A live v1 receipt is an application-mutable lower bound. The held inode is
//! specific to one run, independent of the collector's terminal record set.
use crate::runner::{verify_report_path, Records};
use serde_json::{json, Value};
use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::fs::{FileExt, OpenOptionsExt};
use std::path::PathBuf;

pub(crate) const EXIT_EXCEEDED: i32 = 123;
const SIZE: usize = 48;
const UNIT: &str = "instructions_plus_movs_stos_bytes_v1";

#[derive(Clone, Copy, Default)]
pub(crate) struct Progress {
    counts: [u64; 3],
    overflow: u64,
}

impl Progress {
    pub(crate) fn exceeds(self, limit: u64) -> bool {
        self.overflow != 0 || self.counts.into_iter().map(u128::from).sum::<u128>() > limit.into()
    }

    fn well_formed(self) -> bool {
        self.overflow < 8
            && self
                .counts
                .into_iter()
                .enumerate()
                .all(|(index, count)| self.overflow & (1 << index) == 0 || count == u64::MAX)
    }

    fn contains(self, earlier: Self) -> bool {
        self.overflow & earlier.overflow == earlier.overflow
            && self
                .counts
                .iter()
                .zip(earlier.counts)
                .all(|(n, old)| *n >= old)
    }

    pub(crate) fn matches_terminal(self, measurement: &Value) -> bool {
        let rep = &measurement["rep_breakdown"];
        measurement["valid"] == true
            && measurement["complete"] == true
            && measurement["rep_bytes_enabled"] == true
            && rep["version"] == 1
            && self.overflow == 0
            && measurement["instructions"].as_u64() == Some(self.counts[0])
            && rep["movs_bytes"].as_u64() == Some(self.counts[1])
            && rep["stos_bytes"].as_u64() == Some(self.counts[2])
    }

    pub(crate) fn receipt(self, limit: u64, observation: &str) -> Value {
        json!({"version":1, "unit":UNIT, "limit":limit,
            "status":if self.exceeds(limit) {"exceeded"} else {"within_budget"},
            "observation":observation, "instructions":self.counts[0],
            "movs_bytes":self.counts[1], "stos_bytes":self.counts[2],
            "overflow_mask":self.overflow})
    }
}

pub(crate) fn unavailable(limit: u64) -> Value {
    json!({"version":1,"unit":UNIT,"limit":limit,"status":"unavailable"})
}

/// Reconstruct a closed receipt before relaying an untrusted inner report.
pub(crate) fn validate_receipt(value: &Value, limit: u64, exceeded: bool) -> Option<Progress> {
    let p = Progress {
        counts: [
            value["instructions"].as_u64()?,
            value["movs_bytes"].as_u64()?,
            value["stos_bytes"].as_u64()?,
        ],
        overflow: value["overflow_mask"].as_u64()?,
    };
    let observation = value["observation"].as_str()?;
    (p.well_formed()
        && p.exceeds(limit) == exceeded
        && (observation == "terminal" || (exceeded && observation == "live"))
        && value == &p.receipt(limit, observation))
        .then_some(p)
}

pub(crate) struct Clock {
    pub(crate) directory: Records,
    pub(crate) path: PathBuf,
    file: File,
    previous: Option<Progress>,
    unavailable: bool,
}

impl Clock {
    pub(crate) fn create() -> io::Result<Self> {
        let directory = Records::create(None, false)?;
        let path = directory.path.join("progress-v1");
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        file.set_len(SIZE as u64)?;
        Ok(Self {
            directory,
            path,
            file,
            previous: None,
            unavailable: false,
        })
    }

    fn read(&self) -> io::Result<[u64; 6]> {
        verify_report_path(&self.file, &self.path, SIZE as u64)?;
        let mut bytes = [0u8; SIZE];
        self.file.read_exact_at(&mut bytes, 0)?;
        Ok(std::array::from_fn(|i| {
            u64::from_le_bytes(bytes[i * 8..i * 8 + 8].try_into().unwrap())
        }))
    }

    /// Individual components advance monotonically. A concurrent read can see
    /// different instants, so its sum is a lower bound until all writers exit.
    pub(crate) fn poll(&mut self) -> Option<Progress> {
        if self.unavailable {
            return None;
        }
        match self.read() {
            Ok([0, 0, ..]) if self.previous.is_none() => None,
            Ok([1, 0, i, m, s, overflow]) => {
                let now = Progress {
                    counts: [i, m, s],
                    overflow,
                };
                if now.well_formed() && self.previous.is_none_or(|old| now.contains(old)) {
                    self.previous = Some(now);
                    return Some(now);
                }
                self.unavailable = true;
                None
            }
            _ => {
                self.unavailable = true;
                None
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_thresholds_use_all_three_integer_components() {
        for (counts, overflow, limit, crosses) in [
            ([3, 8, 4], 0, 15, false),
            ([3, 8, 4], 0, 14, true),
            ([0, 0, 1], 0, 0, true),
            (
                [9_007_199_254_740_993, 1, 1],
                0,
                9_007_199_254_740_995,
                false,
            ),
            ([u64::MAX, 1, 0], 0, u64::MAX, true),
            ([0, u64::MAX, 0], 0, u64::MAX, false),
            ([0, u64::MAX, 0], 2, u64::MAX, true),
        ] {
            let p = Progress { counts, overflow };
            assert_eq!(p.exceeds(limit), crosses);
            assert!(validate_receipt(&p.receipt(limit, "terminal"), limit, crosses).is_some());
        }
    }

    fn publish(clock: &Clock, words: [u64; 6]) {
        let bytes = words.map(u64::to_le_bytes).concat();
        clock.file.write_all_at(&bytes, 0).unwrap();
    }

    #[test]
    fn rollback_and_malformed_updates_remain_unavailable_through_exit() {
        for bad in [
            [0, 0, 0, 0, 0, 0],
            [1, 1, 340, 8, 0, 0],
            [1, 0, 339, 8, 0, 0],
            [1, 0, 341, 7, 0, 0],
            [1, 0, 340, 8, 1, 4],
            [1, 0, 340, 8, 0, 8],
        ] {
            let mut clock = Clock::create().unwrap();
            assert!(clock.poll().is_none());
            publish(&clock, [1, 0, 340, 8, 0, 0]);
            assert!(!clock.poll().unwrap().exceeds(348));
            publish(&clock, bad);
            assert!(clock.poll().is_none());
            publish(&clock, [1, 0, 350, 9, 0, 0]);
            assert!(clock.poll().is_none());
        }
    }

    #[test]
    fn terminal_receipt_requires_each_counter_and_eligible_report() {
        let good = json!({"valid":true, "complete":true, "signals_observed":0,
            "unsupported_rep_entries":0,"rep_bytes_enabled":true,"instructions":55,
            "rep_breakdown":{"version":1,"movs_bytes":20,"stos_bytes":0}});
        let p = Progress {
            counts: [55, 20, 0],
            overflow: 0,
        };
        assert!(p.matches_terminal(&good));
        // Signals and CMPS/SCAS entries leave a complete report eligible, as
        // they leave the live counts usable.
        let mut observed = good.clone();
        observed["signals_observed"] = json!(3);
        observed["unsupported_rep_entries"] = json!(2);
        assert!(p.matches_terminal(&observed));
        for (field, other) in [
            ("valid", json!(false)),
            ("complete", json!(false)),
            ("instructions", json!(54)),
            ("rep_breakdown", Value::Null),
            (
                "rep_breakdown",
                json!({"version":1,"movs_bytes":19,"stos_bytes":1}),
            ),
        ] {
            let mut inconsistent = good.clone();
            inconsistent[field] = other;
            assert!(!p.matches_terminal(&inconsistent));
        }
        let receipt = p.receipt(75, "terminal");
        assert!(validate_receipt(&receipt, 74, true).is_none());
        assert!(validate_receipt(&p.receipt(75, "live"), 75, false).is_none());
    }
}
