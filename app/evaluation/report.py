"""
app/evaluation/report.py
===========================
Shared result-writing helpers for the evaluation scripts — CSV/JSON
output plus a printed summary table. Never imported by the live app.
"""

import csv
import json
import os
from datetime import datetime


def write_csv(rows: list, fieldnames: list, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(data, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def timestamped_path(base_dir: str, prefix: str, ext: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(base_dir, f"{prefix}_{ts}.{ext}")


def print_table(headers: list, rows: list, title: str = ""):
    if title:
        print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")
    if not rows:
        print("  (no rows)")
        return
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt_row(cells):
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    print(fmt_row(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt_row(row))
    print()
