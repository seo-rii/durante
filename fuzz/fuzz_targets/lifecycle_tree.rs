#![no_main]
use libfuzzer_sys::fuzz_target;
use serde_json::{json, Value};

fn byte(data: &[u8], n: usize) -> u8 {
    data.get(n).copied().unwrap_or(0)
}
fn count(data: &[u8], n: usize) -> u64 {
    let mut bytes = [0; 8];
    for (i, b) in bytes.iter_mut().enumerate() {
        *b = byte(data, n + i);
    }
    u64::from_le_bytes(bytes)
}
fn event(pid: u32, image: u64, seq: u64, kind: &str) -> Value {
    json!({"schema":1,"client_version":1,"authority":"diagnostic_only",
      "metric":"bb_entry_app_instructions","pid":pid,"segment":format!("{pid}-{image}"),"seq":seq,"event":kind})
}
fn start(pid: u32, image: u64, rep: bool) -> Value {
    let mut row = event(pid, image, 1, "start");
    for (k,v) in json!({"origin":"initial","parent_pid":0,"parent_segment":"","rep_bytes_enabled":rep,"sdk_version":"11.3.0"}).as_object().unwrap() { row[k] = v.clone(); }
    row
}
fn terminal(pid: u32, image: u64, seq: u64, kind: &str, instructions: u64, rep: bool) -> Value {
    let mut row = event(pid, image, seq, kind);
    for (k, v) in json!({"instructions":instructions,"rep_bytes":if rep {8} else {0},
      "rep_entries":if rep {1} else {0},"unsupported_rep_entries":0,"signals_observed":0,
      "threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,
      "app_exit_seen":kind=="end","app_exit_code":0,"valid":true,"quality":"uninterrupted"})
    .as_object()
    .unwrap()
    {
        row[k] = v.clone();
    }
    row
}

// Preserve enough grammar to reach lifecycle/linkage/overflow validation, then
// mutate record semantics, types, graph edges and raw framing independently.
fuzz_target!(|data: &[u8]| {
    let rep = byte(data, 0) & 1 != 0;
    let child = byte(data, 0) & 2 != 0;
    let exec = byte(data, 0) & 4 != 0;
    let failed = byte(data, 0) & 8 != 0;
    let mut rows = vec![start(1, 1, rep)];
    let mut seq = 2;
    if child {
        let mut spawn = event(1, 1, seq, "spawn");
        seq += 1;
        spawn["child_pid"] = json!(2);
        spawn["syscall"] = json!(57);
        spawn["supported"] = json!(true);
        rows.push(spawn);
    }
    let mut image = 1;
    if exec || failed {
        rows.push(terminal(
            1,
            1,
            seq,
            "exec_attempt",
            count(data, 2).max(1),
            rep,
        ));
        seq += 1;
        if failed {
            let mut r = event(1, 1, seq, "exec_failed");
            r["syscall"] = json!(59);
            r["result"] = json!(-2);
            rows.push(r);
            seq += 1;
        } else {
            image = 2;
            rows.push(start(1, image, rep));
            seq = 2;
        }
    }
    rows.push(terminal(1, image, seq, "end", count(data, 10).max(1), rep));
    let mut streams = vec![(1, rows)];
    if child {
        let mut s = start(2, 1, rep);
        s["origin"] = json!("fork");
        s["parent_pid"] = json!(1);
        s["parent_segment"] = json!("1-1");
        streams.push((
            2,
            vec![s, terminal(2, 1, 2, "end", count(data, 18).max(1), rep)],
        ));
    }
    let process = byte(data, 26) as usize % streams.len();
    let row = byte(data, 27) as usize % streams[process].1.len();
    let event = &mut streams[process].1[row];
    match byte(data, 1) % 16 {
        0 => {}
        1 => event["seq"] = json!(count(data, 2)),
        2 => event["pid"] = json!(byte(data, 2)),
        3 => event["parent_pid"] = json!(byte(data, 2)),
        4 => event["instructions"] = json!(true),
        5 => event["valid"] = json!(false),
        6 => event["signals_observed"] = json!(count(data, 2)),
        7 => event["rep_bytes_enabled"] = json!(!rep),
        8 => event["rep_entries"] = json!(0),
        9 => event["threads_finished"] = json!(count(data, 2)),
        10 => event["extra"] = json!({"nested":[1,2,3]}),
        11 => event["result"] = json!(1.0),
        12 => event["supported"] = json!(false),
        13 => event["segment"] = json!("2-1"),
        14 => event["event"] = json!("interrupted"),
        _ => event["authority"] = json!("trusted"),
    }
    let owned: Vec<_> = streams
        .iter()
        .map(|(pid, rows)| {
            let mut bytes = Vec::new();
            for row in rows {
                serde_json::to_writer(&mut bytes, row).unwrap();
                bytes.push(b'\n');
            }
            if byte(data, 28) & 1 != 0 {
                bytes.pop();
            }
            (*pid, bytes)
        })
        .collect();
    let inputs: Vec<_> = owned.iter().map(|(pid, b)| (*pid, b.as_slice())).collect();
    let result = durante::collect::collect_bytes(&inputs, 1);
    durante_fuzz::assert_outcome(&result);
    if result["valid"] == true {
        let mut reversed = inputs.clone();
        reversed.reverse();
        assert_eq!(result, durante::collect::collect_bytes(&reversed, 1));
        let mut duplicate = inputs;
        duplicate.push(duplicate[0]);
        let rejected = durante::collect::collect_bytes(&duplicate, 1);
        assert_eq!(rejected["valid"], false);
        durante_fuzz::assert_outcome(&rejected);
    }
});
