use durante::collect::{collect, MAX_EVENTS, MAX_FILE_BYTES, MAX_LINE_BYTES, MAX_PROCESSES};
use serde_json::{json, Value};
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::symlink;
use std::path::Path;

fn event(pid: u32, image: u64, seq: u64, kind: &str) -> Value {
    json!({"schema":1,"client_version":1,"authority":"diagnostic_only",
           "metric":"bb_entry_app_instructions","pid":pid,
           "segment":format!("{pid}-{image}"),"seq":seq,"event":kind})
}
fn start(pid: u32, image: u64) -> Value {
    let mut row = event(pid, image, 1, "start");
    for (key, value) in json!({"origin":"initial","parent_pid":0,"parent_segment":"",
                              "rep_bytes_enabled":false,"sdk_version":"11.3.0"})
    .as_object()
    .unwrap()
    {
        row[key] = value.clone();
    }
    row
}
fn end(pid: u32, image: u64, seq: u64, count: u64) -> Value {
    let mut row = event(pid, image, seq, "end");
    for (key, value) in json!({"instructions":count,"rep_bytes":0,"rep_entries":0,
      "unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,
      "threads_finished":1,"threads_complete":true,"accounting_error":false,
      "app_exit_seen":true,"app_exit_code":0,"valid":true,"quality":"uninterrupted"})
    .as_object()
    .unwrap()
    {
        row[key] = value.clone();
    }
    row
}
fn write_rows(directory: &Path, pid: u32, rows: &[Value]) {
    let mut bytes = Vec::new();
    for row in rows {
        serde_json::to_writer(&mut bytes, row).unwrap();
        bytes.push(b'\n');
    }
    fs::write(directory.join(format!("process-{pid}.jsonl")), bytes).unwrap();
}
fn assert_invalid(result: &Value) {
    assert_eq!(result["valid"], false, "{result}");
    assert_eq!(result["complete"], false, "{result}");
    assert_eq!(result["authority"], "diagnostic_only");
    assert!(result["reason"]
        .as_str()
        .is_some_and(|s| !s.is_empty() && s.len() <= 300));
    for field in [
        "instructions",
        "rep_bytes",
        "rep_entries",
        "unsupported_rep_entries",
        "signals_observed",
        "processes",
        "segments",
    ] {
        assert!(
            result.get(field).is_none(),
            "invalid aggregate exposed partial {field}: {result}"
        );
    }
}

#[test]
fn shared_protocol_vectors() {
    let vectors: Value = serde_json::from_str(include_str!("protocol_vectors.json")).unwrap();
    for case in vectors["cases"].as_array().unwrap() {
        let directory = tempfile::tempdir().unwrap();
        for (name, rows) in case["files"].as_object().unwrap() {
            let pid = name
                .strip_prefix("process-")
                .unwrap()
                .strip_suffix(".jsonl")
                .unwrap()
                .parse()
                .unwrap();
            write_rows(directory.path(), pid, rows.as_array().unwrap());
        }
        let result = collect(directory.path(), case["root_pid"].as_u64().unwrap() as u32);
        assert_eq!(
            result["valid"], case["valid"],
            "case {}: {result}",
            case["name"]
        );
        if case["valid"] == true {
            for (key, expected) in case["expected"].as_object().unwrap() {
                assert_eq!(&result[key], expected, "case {} field {key}", case["name"]);
            }
        } else {
            assert_invalid(&result);
        }
    }
}

#[test]
fn one_process_matches_reference_shape() {
    let dir = tempfile::tempdir().unwrap();
    write_rows(dir.path(), 1, &[start(1, 1), end(1, 1, 2, 100)]);
    let result = collect(dir.path(), 1);
    let counters = json!({"instructions":100,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0});
    let mut process = json!({"pid":1,"parent_pid":0,"parent_segment":"","images":1,"exit_code":0});
    let mut segment = json!({"pid":1,"segment":"1-1","threads":1,"exit_code":0});
    for (key, value) in counters.as_object().unwrap() {
        process[key] = value.clone();
        segment[key] = value.clone();
    }
    let mut expected = json!({"schema":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions",
     "scope":"process_tree_user_code","valid":true,"complete":true,"root_pid":1,
     "process_count":1,"image_count":1,"rep_bytes_enabled":false,"quality":"uninterrupted",
     "processes":[process],"segments":[segment],
     "limits":{"max_processes":256,"max_file_bytes":1048576,"max_total_bytes":16777216,"max_events_per_process":4096}});
    for (key, value) in counters.as_object().unwrap() {
        expected[key] = value.clone();
    }
    assert_eq!(result, expected);
}

#[test]
fn lexical_file_order_is_distinct_from_numeric_process_order() {
    let dir = tempfile::tempdir().unwrap();
    let mut spawn = event(9, 1, 2, "spawn");
    spawn["child_pid"] = json!(10);
    spawn["syscall"] = json!(57);
    spawn["supported"] = json!(true);
    let mut child = start(10, 1);
    child["origin"] = json!("fork");
    child["parent_pid"] = json!(9);
    child["parent_segment"] = json!("9-1");
    write_rows(dir.path(), 9, &[start(9, 1), spawn, end(9, 1, 3, 100)]);
    write_rows(dir.path(), 10, &[child, end(10, 1, 2, 30)]);
    let result = collect(dir.path(), 9);
    assert_eq!(result["instructions"], 130);
    assert_eq!(result["processes"][0]["pid"], 9);
    assert_eq!(result["segments"][0]["pid"], 10);
}

#[test]
fn exec_checkpoint_and_failed_attempt_are_not_double_counted() {
    let dir = tempfile::tempdir().unwrap();
    let mut checkpoint = end(1, 1, 2, 100);
    checkpoint["event"] = json!("exec_attempt");
    checkpoint["app_exit_seen"] = json!(false);
    write_rows(
        dir.path(),
        1,
        &[
            start(1, 1),
            checkpoint.clone(),
            start(1, 2),
            end(1, 2, 2, 25),
        ],
    );
    let result = collect(dir.path(), 1);
    assert_eq!(result["instructions"], 125);
    assert!(result["segments"][0]["exit_code"].is_null());
    let mut failed = event(1, 1, 3, "exec_failed");
    failed["syscall"] = json!(59);
    failed["result"] = json!(-2);
    write_rows(
        dir.path(),
        1,
        &[start(1, 1), checkpoint, failed, end(1, 1, 4, 125)],
    );
    let result = collect(dir.path(), 1);
    assert_eq!(result["instructions"], 125);
    assert_eq!(result["image_count"], 1);
}

#[test]
fn normal_nonzero_exit_and_signal_quality_remain_auditable() {
    let dir = tempfile::tempdir().unwrap();
    let mut final_row = end(1, 1, 2, 100);
    final_row["app_exit_code"] = json!(7);
    final_row["signals_observed"] = json!(1);
    final_row["quality"] = json!("signals_observed");
    write_rows(dir.path(), 1, &[start(1, 1), final_row]);
    let result = collect(dir.path(), 1);
    assert_eq!(result["valid"], true);
    assert_eq!(result["quality"], "signals_observed");
    assert_eq!(result["processes"][0]["exit_code"], 7);
}

#[test]
fn rejects_lossy_integer_types_and_out_of_range_values() {
    let dir = tempfile::tempdir().unwrap();
    for field in ["schema", "client_version", "pid", "seq", "parent_pid"] {
        for value in [json!(true), json!(1.0), Value::Null, json!("1")] {
            let mut row = start(1, 1);
            row[field] = value;
            write_rows(dir.path(), 1, &[row, end(1, 1, 2, 100)]);
            assert_invalid(&collect(dir.path(), 1));
        }
    }
    for field in [
        "instructions",
        "rep_bytes",
        "rep_entries",
        "unsupported_rep_entries",
        "signals_observed",
        "threads_started",
        "threads_finished",
        "app_exit_code",
    ] {
        for value in [json!(true), json!(1.0), json!(-1), Value::Null] {
            let mut row = end(1, 1, 2, 100);
            row[field] = value;
            write_rows(dir.path(), 1, &[start(1, 1), row]);
            assert_invalid(&collect(dir.path(), 1));
        }
    }
    let mut row = end(1, 1, 2, 100);
    row["app_exit_code"] = json!(256);
    write_rows(dir.path(), 1, &[start(1, 1), row]);
    assert_invalid(&collect(dir.path(), 1));
    let raw = format!("{}\n{}\n", start(1, 1), end(1, 1, 2, 100)).replace(
        "\"instructions\":100",
        "\"instructions\":18446744073709551616",
    );
    fs::write(dir.path().join("process-1.jsonl"), raw).unwrap();
    assert_invalid(&collect(dir.path(), 1));
}

#[test]
fn duplicate_nested_nonfinite_and_non_utf8_records_are_rejected_without_echo() {
    let dir = tempfile::tempdir().unwrap();
    for data in [
        b"{\"pid\":1,\"pid\":1}\n".as_slice(),
        b"{\"untrusted marker\":1,\"untrusted marker\":2}\n",
        b"{\"pid\":1,\"unknown\":{\"nested\":1}}\n",
        b"{\"pid\":1,\"unknown\":[1]}\n",
        b"{\"pid\":1,\"unknown\":NaN}\n",
        b"{\"pid\":1,\"unknown\":Infinity}\n",
        b"{\"pid\":1,\"unknown\":\"\xff\"}\n",
        b"{\"pid\":1}\n{} {}\n",
    ] {
        fs::write(dir.path().join("process-1.jsonl"), data).unwrap();
        let result = collect(dir.path(), 1);
        assert_invalid(&result);
        assert!(!result["reason"]
            .as_str()
            .unwrap()
            .contains("untrusted marker"));
    }
}

#[test]
fn file_and_directory_symlinks_hardlinks_fifos_and_directories_fail_closed() {
    let outside = tempfile::tempdir().unwrap();
    let dir = tempfile::tempdir().unwrap();
    write_rows(outside.path(), 1, &[start(1, 1), end(1, 1, 2, 100)]);
    let record = dir.path().join("process-1.jsonl");
    symlink(outside.path().join("process-1.jsonl"), &record).unwrap();
    assert_invalid(&collect(dir.path(), 1));
    fs::remove_file(&record).unwrap();
    fs::hard_link(outside.path().join("process-1.jsonl"), &record).unwrap();
    assert_invalid(&collect(dir.path(), 1));
    fs::remove_file(&record).unwrap();
    fs::create_dir(&record).unwrap();
    assert_invalid(&collect(dir.path(), 1));
    fs::remove_dir(&record).unwrap();
    let c_path = std::ffi::CString::new(record.as_os_str().as_bytes()).unwrap();
    // SAFETY: mkfifo receives a valid NUL-terminated owned fixture path.
    assert_eq!(unsafe { libc::mkfifo(c_path.as_ptr(), 0o600) }, 0);
    let before = std::time::Instant::now();
    assert_invalid(&collect(dir.path(), 1));
    assert!(before.elapsed() < std::time::Duration::from_secs(2));
    fs::remove_file(&record).unwrap();
    let link = dir.path().join("alias");
    symlink(outside.path(), &link).unwrap();
    assert_invalid(&collect(&link, 1));
}

#[test]
fn truncated_empty_crlf_and_oversize_records_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let record = dir.path().join("process-1.jsonl");
    let valid = format!("{}\n{}\n", start(1, 1), end(1, 1, 2, 100));
    for text in [
        valid.trim_end_matches('\n').to_owned(),
        valid.replace('\n', "\r\n"),
        format!("\n{valid}"),
        String::new(),
    ] {
        fs::write(&record, text).unwrap();
        assert_invalid(&collect(dir.path(), 1));
    }
    let mut line = start(1, 1).to_string();
    line.extend(std::iter::repeat_n(' ', MAX_LINE_BYTES - line.len()));
    line.push('\n');
    fs::write(&record, line).unwrap();
    assert_invalid(&collect(dir.path(), 1));
    fs::write(&record, vec![b' '; MAX_FILE_BYTES + 1]).unwrap();
    assert_invalid(&collect(dir.path(), 1));
}

#[test]
fn entry_and_event_enumeration_have_fixed_bounds() {
    let dir = tempfile::tempdir().unwrap();
    for pid in 1..=MAX_PROCESSES + 1 {
        fs::write(dir.path().join(format!("process-{pid}.jsonl")), b"x\n").unwrap();
    }
    assert_invalid(&collect(dir.path(), 1));
    let dir = tempfile::tempdir().unwrap();
    write_rows(dir.path(), 1, &vec![start(1, 1); MAX_EVENTS + 1]);
    assert_invalid(&collect(dir.path(), 1));
}

#[test]
fn total_file_budget_is_enforced_before_aggregation() {
    let dir = tempfile::tempdir().unwrap();
    for pid in 1..=17 {
        let mut row = start(pid, 1);
        row["padding"] = json!("x".repeat(2400));
        let line = format!("{row}\n");
        assert!(line.len() < MAX_LINE_BYTES);
        fs::write(
            dir.path().join(format!("process-{pid}.jsonl")),
            line.repeat(MAX_FILE_BYTES / line.len()),
        )
        .unwrap();
    }
    let result = collect(dir.path(), 1);
    assert_invalid(&result);
    assert_eq!(result["reason"], "total report size exceeded");
}

#[test]
fn malformed_names_root_ids_and_missing_root_are_rejected() {
    assert_invalid(&collect(
        Path::new("/does-not-exist/durante-collector-fixture"),
        1,
    ));
    for name in [
        "process-0.jsonl",
        "process-01.jsonl",
        "process-2147483648.jsonl",
        "process-1.jsonl.extra",
        "anything",
    ] {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join(name), b"{}\n").unwrap();
        assert_invalid(&collect(dir.path(), 1));
    }
    let dir = tempfile::tempdir().unwrap();
    write_rows(dir.path(), 1, &[start(1, 1), end(1, 1, 2, 100)]);
    for root in [0, 2, u32::MAX] {
        assert_invalid(&collect(dir.path(), root));
    }
}

#[test]
fn byte_collection_matches_files_and_rejects_duplicate_streams_and_impossible_rep() {
    use durante::collect::collect_bytes;
    let dir = tempfile::tempdir().unwrap();
    let rows = [start(1, 1), end(1, 1, 2, 100)];
    write_rows(dir.path(), 1, &rows);
    let bytes = fs::read(dir.path().join("process-1.jsonl")).unwrap();
    assert_eq!(collect_bytes(&[(1, &bytes)], 1), collect(dir.path(), 1));
    assert_invalid(&collect_bytes(&[(1, &bytes), (1, &bytes)], 1));
    assert_invalid(&collect_bytes(&[], 1));
    assert_invalid(&collect_bytes(&[(0, &bytes)], 0));
    let mut rows = rows;
    rows[0]["rep_bytes_enabled"] = json!(true);
    rows[1]["rep_bytes"] = json!(1);
    write_rows(dir.path(), 1, &rows);
    assert_invalid(&collect(dir.path(), 1));
}

#[test]
fn deeply_nested_unknown_fields_never_recurse_or_expose_totals() {
    use durante::collect::collect_bytes;
    for token in ["[", "{"] {
        let close = if token == "[" { "]" } else { "}" };
        let nested = if token == "[" {
            format!("{}0{}", token.repeat(1100), close.repeat(1100))
        } else {
            format!("{}0{}", "{\"a\":".repeat(400), close.repeat(400))
        };
        let bytes = format!("{{\"pid\":1,\"extra\":{nested}}}\n");
        assert_invalid(&collect_bytes(&[(1, bytes.as_bytes())], 1));
    }
}
