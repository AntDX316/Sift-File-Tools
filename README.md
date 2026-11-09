# Sift File Tools

Created by [@AntDX316](https://github.com/AntDX316).

Blazing-fast duplicate file finder and fuzzy file search — a **Python CLI
frontend over a compiled Rust engine**.

```
┌────────────────────┐   JSON (stdout)    ┌──────────────────────────┐
│  sift.py (Python)  │ ─────────────────▶ │  sift (Rust binary)      │
│  argparse · colors │ ◀───────────────── │  rayon · walkdir · blake3│
│  pretty printing   │   progress (stderr)│  nucleo fuzzy matcher    │
└────────────────────┘                    └──────────────────────────┘
```

The Rust engine does the heavy lifting in parallel across all CPU cores;
Python handles UX. No Python packaging needed — just `cargo` + `python`.

## Setup

```sh
cargo build --release     # builds target/release/sift(.exe)
python make_testdata.py   # optional: ~600 files / ~200 MB of demo data
```

## Usage

```sh
python sift.py scan [PATH] [--min-size BYTES] [--hidden] [--json]
python sift.py find QUERY [PATH] [-n TOP] [--hidden] [--json]
```

### Find duplicates

```sh
python sift.py scan testdata
```

```
scanned 600 files · hashed 240 · 40 duplicate group(s) · 96.4 MB reclaimable

#1  1.8 MB × 3 copies  (3.6 MB wasted)
  hash 9f2c…
    • testdata\dir00\report_final_0.bin
    • testdata\dir05\report_final_40.bin
    …
```

The engine groups files by size first and only hashes files whose size is
shared by more than one file — on a typical tree that's a tiny fraction of
everything, so scans stay fast even on huge directories.

### Fuzzy file search

```sh
python sift.py find rpt testdata -n 10
```

fzf-style scoring — `rpt` matches `report_final_0.bin`, `export_csv`, etc.
Matched characters are highlighted in the output.

### Machine-readable output

Add `--json` to either subcommand (or call the engine directly — it always
emits JSON on stdout and progress on stderr).

## How it's fast

- **rayon** — file hashing and fuzzy scoring run on every CPU core
- **BLAKE3** — one of the fastest cryptographic hashes (~GB/s per core)
- **size pre-filter** — only files with colliding sizes get hashed
- **streaming I/O** — 1 MiB buffered reads, constant memory per file

### Benchmark

`python make_testdata.py` → 600 files / ~300 MB, 40 duplicate groups:

| implementation | time |
|---|---|
| pure Python (`os.walk` + `hashlib.blake2b`, single-threaded) | 234 ms |
| sift Rust engine (rayon + blake3 + size pre-filter) | **34 ms** |

~7× faster end-to-end on this corpus — and the gap widens with tree size,
since hashing parallelizes across cores while Python stays single-threaded.

## Layout

| file | purpose |
|---|---|
| `src/main.rs` | Rust engine: `scan` + `find` subcommands, JSON output |
| `sift.py` | Python CLI: args, auto-build, colored rendering |
| `make_testdata.py` | generates `testdata/` demo corpus |
| `Cargo.toml` | engine dependencies |
