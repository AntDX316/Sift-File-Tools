#!/usr/bin/env python3
"""
sift — blazing-fast duplicate finder & fuzzy file search.

Python CLI frontend over a compiled Rust engine (target/release/sift*).
The Rust core does the heavy lifting — parallel BLAKE3 hashing across all
CPU cores and fzf-style fuzzy matching — while this script handles
argument parsing and pretty terminal output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- colors


def _tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


if _tty():
    BOLD, DIM = "\033[1m", "\033[2m"
    CYAN, MAG, YEL, GRN, RED = "\033[36m", "\033[35m", "\033[33m", "\033[32m", "\033[31m"
    RST = "\033[0m"
else:
    BOLD = DIM = CYAN = MAG = YEL = GRN = RED = RST = ""


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------------------------------------------------------------- engine


def find_engine() -> str:
    candidates = [
        os.path.join(ROOT, "target", "release", "sift.exe"),
        os.path.join(ROOT, "target", "release", "sift"),
        os.path.join(ROOT, "target", "debug", "sift.exe"),
        os.path.join(ROOT, "target", "debug", "sift"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    if shutil.which("sift"):
        return "sift"

    print(f"{DIM}Rust engine not built yet — running `cargo build --release`…{RST}", file=sys.stderr)
    subprocess.run(["cargo", "build", "--release"], cwd=ROOT, check=True)
    for c in candidates[:2]:
        if os.path.isfile(c):
            return c
    sys.exit(f"{RED}error: could not locate the built engine{RST}")


def run_engine(engine: str, args: list[str]) -> dict:
    # stderr is inherited so the Rust progress line shows live on the terminal;
    # stdout is captured because it carries the JSON payload.
    proc = subprocess.run(
        [engine, *args],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.exit(f"{RED}engine failed (exit {proc.returncode}){RST}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------- render

MAX_FILES_SHOWN = 25


def render_scan(d: dict) -> None:
    groups = d["groups"]
    print(
        f"{DIM}scanned {d['files_scanned']:,} files · "
        f"hashed {d['files_hashed']:,} · "
        f"{d['duplicate_groups']} duplicate group(s) · "
        f"{human(d['total_wasted'])} reclaimable{RST}"
    )
    if not groups:
        print(f"{GRN}no duplicates found{RST}")
        return
    for i, g in enumerate(groups, 1):
        print()
        print(
            f"{MAG}{BOLD}#{i}{RST}  {CYAN}{human(g['size'])}{RST} × "
            f"{BOLD}{g['copies']}{RST} copies  {DIM}({human(g['wasted'])} wasted){RST}"
        )
        print(f"  {DIM}hash {g['hash'][:16]}…{RST}")
        for f in g["files"][:MAX_FILES_SHOWN]:
            print(f"    {YEL}•{RST} {f}")
        if len(g["files"]) > MAX_FILES_SHOWN:
            print(f"    {DIM}… and {len(g['files']) - MAX_FILES_SHOWN} more{RST}")
    print(f"\n{DIM}tip: re-run with --json for machine-readable output{RST}")


def _highlight(name: str, query: str) -> str:
    """Bold the characters of `name` that fuzzy-match `query`, in order."""
    qi = 0
    out = []
    ql = query.lower()
    for ch in name:
        if qi < len(ql) and ch.lower() == ql[qi]:
            out.append(f"{BOLD}{CYAN}{ch}{RST}")
            qi += 1
        else:
            out.append(ch)
    return "".join(out)


def render_find(d: dict, query: str) -> None:
    print(f"{DIM}searched {d['files_scanned']:,} files for {BOLD}'{query}'{RST}")
    if not d["results"]:
        print(f"{RED}no matches{RST}")
        return
    for hit in d["results"]:
        path = hit["path"]
        name = os.path.basename(path)
        parent = path[: -len(name)] if name else path
        print(f"  {GRN}▸{RST} {parent}{_highlight(name, query)}  {DIM}score {hit['score']}{RST}")


# ---------------------------------------------------------------- cli


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(
        prog="sift",
        description="Blazing-fast duplicate finder & fuzzy file search (Rust engine + Python CLI).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="find duplicate files by content hash")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--min-size", type=int, default=1, metavar="BYTES",
                   help="minimum file size to consider (default: 1)")
    s.add_argument("--hidden", action="store_true", help="include hidden files/dirs")
    s.add_argument("--follow-links", action="store_true", help="follow symlinks")
    s.add_argument("--json", action="store_true", help="emit raw JSON")

    f = sub.add_parser("find", help="fuzzy-search file names")
    f.add_argument("query", help="fuzzy query (e.g. 'rpt2024')")
    f.add_argument("path", nargs="?", default=".")
    f.add_argument("-n", "--top", type=int, default=50, help="max results (default: 50)")
    f.add_argument("--hidden", action="store_true", help="include hidden files/dirs")
    f.add_argument("--follow-links", action="store_true", help="follow symlinks")
    f.add_argument("--json", action="store_true", help="emit raw JSON")

    args = p.parse_args()
    engine = find_engine()

    if args.cmd == "scan":
        eargs = ["scan", "--min-size", str(args.min_size)]
        if args.hidden:
            eargs.append("--hidden")
        if args.follow_links:
            eargs.append("--follow-links")
        eargs += ["--", args.path]
        data = run_engine(engine, eargs)
        if args.json:
            print(json.dumps(data))
        else:
            render_scan(data)
    else:
        eargs = ["find", "--top", str(args.top)]
        if args.hidden:
            eargs.append("--hidden")
        if args.follow_links:
            eargs.append("--follow-links")
        eargs += ["--", args.query, args.path]
        data = run_engine(engine, eargs)
        if args.json:
            print(json.dumps(data))
        else:
            render_find(data, args.query)


if __name__ == "__main__":
    main()
