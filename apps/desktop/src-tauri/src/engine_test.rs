use std::path::Path;

use crate::engine::{engine_executable_candidates, parse_ready_line};

#[test]
fn parses_bound_engine_line_without_a_token() {
    let ready = parse_ready_line(r#"{"event":"engine_bound","port":49152}"#).unwrap();
    assert_eq!(ready.port, 49152);
}

#[test]
fn rejects_unexpected_or_out_of_range_ready_lines() {
    assert!(parse_ready_line(r#"{"event":"ready","port":49152}"#).is_err());
    assert!(parse_ready_line(r#"{"event":"engine_bound","port":0}"#).is_err());
}

#[test]
fn rejects_ready_lines_that_contain_secrets_or_extra_fields() {
    assert!(parse_ready_line(r#"{"event":"engine_bound","port":49152,"token":"secret"}"#).is_err());
}

#[test]
fn resolves_packaged_engine_before_the_development_layout() {
    let candidates = engine_executable_candidates(Path::new(r"C:\Program Files\AI4Music"));

    assert_eq!(
        candidates,
        [
            Path::new(r"C:\Program Files\AI4Music\engine\ai4music-engine.exe").to_path_buf(),
            Path::new(r"C:\Program Files\AI4Music\resources\engine\ai4music-engine.exe")
                .to_path_buf(),
        ]
    );
}
