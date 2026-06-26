use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
struct Request {
    op: String,
    root: PathBuf,
    path: PathBuf,
    pattern: String,
    #[serde(default = "default_max_results")]
    max_results: usize,
}

fn default_max_results() -> usize {
    50
}

#[derive(Debug, Serialize)]
struct Match {
    path: String,
    line_number: usize,
    line: String,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    code: String,
    message: String,
}

#[derive(Debug, Serialize)]
struct Response {
    ok: bool,
    backend: &'static str,
    matches: Vec<Match>,
    files_searched: usize,
    truncated: bool,
    truncated_reason: Option<&'static str>,
    error: Option<ErrorBody>,
}

fn main() {
    let response = run().unwrap_or_else(|message| Response {
        ok: false,
        backend: "rust-sidecar-poc",
        matches: Vec::new(),
        files_searched: 0,
        truncated: false,
        truncated_reason: None,
        error: Some(ErrorBody {
            code: "sidecar_error".to_string(),
            message,
        }),
    });
    println!("{}", serde_json::to_string(&response).expect("serialize response"));
    if !response.ok {
        std::process::exit(2);
    }
}

fn run() -> Result<Response, String> {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .map_err(|err| format!("read stdin: {err}"))?;
    let request: Request = serde_json::from_str(&input).map_err(|err| format!("parse request: {err}"))?;
    if request.op != "grep" {
        return Err(format!("unsupported op: {}", request.op));
    }
    let root = canonicalize_existing(&request.root)?;
    let path = canonicalize_existing(&request.path)?;
    if !path.starts_with(&root) {
        return Err("path escapes root".to_string());
    }

    let mut matches = Vec::new();
    let mut files_searched = 0usize;
    walk_grep(
        &root,
        &path,
        &request.pattern,
        request.max_results,
        &mut files_searched,
        &mut matches,
    );
    let truncated = matches.len() >= request.max_results;
    Ok(Response {
        ok: true,
        backend: "rust-sidecar-poc",
        matches,
        files_searched,
        truncated,
        truncated_reason: if truncated { Some("max_results") } else { None },
        error: None,
    })
}

fn canonicalize_existing(path: &Path) -> Result<PathBuf, String> {
    path.canonicalize()
        .map_err(|err| format!("canonicalize {}: {err}", path.display()))
}

fn walk_grep(
    root: &Path,
    path: &Path,
    pattern: &str,
    max_results: usize,
    files_searched: &mut usize,
    matches: &mut Vec<Match>,
) {
    if matches.len() >= max_results {
        return;
    }
    if path.is_dir() {
        let entries = match fs::read_dir(path) {
            Ok(entries) => entries,
            Err(_) => return,
        };
        for entry in entries.flatten() {
            let child = entry.path();
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if matches.len() >= max_results {
                return;
            }
            if name == ".git" || name == "node_modules" || name == ".venv" || name == "__pycache__" {
                continue;
            }
            walk_grep(root, &child, pattern, max_results, files_searched, matches);
        }
        return;
    }
    if !path.is_file() {
        return;
    }
    let Ok(bytes) = fs::read(path) else {
        return;
    };
    if bytes.iter().take(4096).any(|byte| *byte == 0) {
        return;
    }
    *files_searched += 1;
    let text = String::from_utf8_lossy(&bytes);
    for (idx, line) in text.lines().enumerate() {
        if matches.len() >= max_results {
            return;
        }
        if line.contains(pattern) {
            let rel = path.strip_prefix(root).unwrap_or(path);
            matches.push(Match {
                path: rel.to_string_lossy().replace('\\', "/"),
                line_number: idx + 1,
                line: line.to_string(),
            });
        }
    }
}
