#!/usr/bin/env python3
"""Generate testdata/ with duplicate files for demos and benchmarks."""
import os
import random
import shutil

random.seed(42)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


def main() -> None:
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)

    payloads = [os.urandom(random.randint(50_000, 2_000_000)) for _ in range(40)]
    names = [
        "report_final", "data_backup", "photo_dump", "archive_old", "logs_2024",
        "dataset_v2", "video_clip", "doc_scan", "music_mix", "assets_pack",
        "export_csv", "snapshot_db", "build_cache", "mail_store", "notes_sync",
    ]

    # ~600 files; most unique, ~120 are deliberate duplicates
    for i in range(600):
        sub = os.path.join(ROOT, f"dir{i % 12:02d}")
        os.makedirs(sub, exist_ok=True)
        if i < 120:
            content = payloads[i % 40]
            name = f"{random.choice(names)}_{i}.bin"
        else:
            content = os.urandom(random.randint(10_000, 800_000))
            name = f"{random.choice(names)}_{i}.dat"
        with open(os.path.join(sub, name), "wb") as f:
            f.write(content)

    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(ROOT) for f in fs)
    print(f"created {ROOT} — 600 files, {total / 1e6:.1f} MB total")


if __name__ == "__main__":
    main()
