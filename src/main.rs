use clap::{Parser, Subcommand};
use nucleo_matcher::pattern::{CaseMatching, Normalization, Pattern};
use nucleo_matcher::{Config, Matcher, Utf32Str};
use rayon::prelude::*;
use serde::Serialize;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufReader};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use walkdir::WalkDir;

#[derive(Parser)]
#[command(
    name = "sift",
    version,
    about = "Blazing-fast duplicate finder and fuzzy file search — Rust engine, JSON output"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Find duplicate files by content (parallel BLAKE3 hashing)
    Scan {
        /// Root directory to scan
        #[arg(default_value = ".")]
        path: PathBuf,
        /// Minimum file size in bytes to consider
        #[arg(long, default_value_t = 1)]
        min_size: u64,
        /// Include hidden files and directories
        #[arg(long)]
        hidden: bool,
        /// Follow symbolic links
        #[arg(long)]
        follow_links: bool,
    },
    /// Fuzzy-search file names (fzf-style scoring)
    Find {
        /// Fuzzy query
        query: String,
        /// Root directory to search
        #[arg(default_value = ".")]
        path: PathBuf,
        /// Maximum number of results
        #[arg(short, long, default_value_t = 50)]
        top: usize,
        /// Include hidden files and directories
        #[arg(long)]
        hidden: bool,
        /// Follow symbolic links
        #[arg(long)]
        follow_links: bool,
    },
}

#[derive(Serialize)]
struct ScanGroup {
    size: u64,
    hash: String,
    copies: u64,
    wasted: u64,
    files: Vec<String>,
}

#[derive(Serialize)]
struct ScanResult {
    root: String,
    files_scanned: u64,
    files_hashed: u64,
    duplicate_groups: u64,
    total_wasted: u64,
    groups: Vec<ScanGroup>,
}

#[derive(Serialize)]
struct FindHit {
    path: String,
    score: u32,
}

#[derive(Serialize)]
struct FindResult {
    root: String,
    files_scanned: u64,
    results: Vec<FindHit>,
}

fn is_hidden(entry: &walkdir::DirEntry) -> bool {
    entry.depth() > 0 && entry.file_name().to_string_lossy().starts_with('.')
}

fn collect_files(root: &Path, hidden: bool, follow_links: bool) -> Vec<(PathBuf, u64)> {
    WalkDir::new(root)
        .follow_links(follow_links)
        .into_iter()
        .filter_entry(|e| hidden || !is_hidden(e))
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
        .filter_map(|e| {
            let len = e.metadata().ok()?.len();
            Some((e.path().to_path_buf(), len))
        })
        .collect()
}

fn hash_file(path: &Path) -> io::Result<String> {
    let file = File::open(path)?;
    let mut hasher = blake3::Hasher::new();
    hasher.update_reader(BufReader::with_capacity(1 << 20, file))?;
    Ok(hasher.finalize().to_hex().to_string())
}

fn spawn_progress(total: usize) -> Arc<AtomicUsize> {
    let counter = Arc::new(AtomicUsize::new(0));
    if total > 0 {
        let c = Arc::clone(&counter);
        std::thread::spawn(move || {
            loop {
                std::thread::sleep(Duration::from_millis(250));
                let n = c.load(Ordering::Relaxed);
                eprint!("\r\x1b[K  hashing {n}/{total} files");
                if n >= total {
                    break;
                }
            }
            eprintln!("\r\x1b[K  hashed {total} files");
        });
    }
    counter
}

fn run_scan(path: &Path, min_size: u64, hidden: bool, follow_links: bool) {
    let files = collect_files(path, hidden, follow_links);
    let total_files = files.len();

    // Group by size first — only files sharing a size can possibly be
    // duplicates, so we skip hashing the vast majority of a typical tree.
    let mut by_size: HashMap<u64, Vec<usize>> = HashMap::new();
    for (i, (_, size)) in files.iter().enumerate() {
        if *size >= min_size {
            by_size.entry(*size).or_default().push(i);
        }
    }
    let candidates: Vec<usize> = by_size
        .values()
        .filter(|v| v.len() > 1)
        .flatten()
        .copied()
        .collect();

    let progress = spawn_progress(candidates.len());
    let hashed: Vec<(usize, String)> = candidates
        .par_iter()
        .filter_map(|&i| {
            let hash = hash_file(&files[i].0).ok()?;
            progress.fetch_add(1, Ordering::Relaxed);
            Some((i, hash))
        })
        .collect();
    let files_hashed = hashed.len();

    let mut groups: HashMap<(u64, String), Vec<String>> = HashMap::new();
    for (i, hash) in hashed {
        let (p, size) = &files[i];
        groups
            .entry((*size, hash))
            .or_default()
            .push(p.display().to_string());
    }

    let mut out: Vec<ScanGroup> = groups
        .into_iter()
        .filter(|(_, v)| v.len() > 1)
        .map(|((size, hash), mut paths)| {
            paths.sort();
            ScanGroup {
                size,
                hash,
                copies: paths.len() as u64,
                wasted: size * (paths.len() as u64 - 1),
                files: paths,
            }
        })
        .collect();
    out.sort_by(|a, b| b.wasted.cmp(&a.wasted));

    let result = ScanResult {
        root: path.display().to_string(),
        files_scanned: total_files as u64,
        files_hashed: files_hashed as u64,
        duplicate_groups: out.len() as u64,
        total_wasted: out.iter().map(|g| g.wasted).sum(),
        groups: out,
    };
    println!("{}", serde_json::to_string(&result).expect("serialize"));
}

fn run_find(query: &str, path: &Path, top: usize, hidden: bool, follow_links: bool) {
    let files = collect_files(path, hidden, follow_links);
    let pattern = Pattern::parse(query, CaseMatching::Smart, Normalization::Smart);

    let mut hits: Vec<FindHit> = files
        .par_iter()
        .map_init(
            || (Matcher::new(Config::DEFAULT), Vec::new()),
            |(matcher, buf), (p, _)| {
                let name = p.file_name()?.to_str()?;
                let score = pattern.score(Utf32Str::new(name, buf), matcher)?;
                Some(FindHit {
                    path: p.display().to_string(),
                    score,
                })
            },
        )
        .flatten()
        .collect();

    hits.sort_by(|a, b| b.score.cmp(&a.score).then_with(|| a.path.cmp(&b.path)));
    hits.truncate(top);

    let result = FindResult {
        root: path.display().to_string(),
        files_scanned: files.len() as u64,
        results: hits,
    };
    println!("{}", serde_json::to_string(&result).expect("serialize"));
}

fn main() {
    match Cli::parse().command {
        Command::Scan {
            path,
            min_size,
            hidden,
            follow_links,
        } => run_scan(&path, min_size, hidden, follow_links),
        Command::Find {
            query,
            path,
            top,
            hidden,
            follow_links,
        } => run_find(&query, &path, top, hidden, follow_links),
    }
}
