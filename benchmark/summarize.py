"""Summarise the benchmark results as text tables (no figures).

    python summarize.py [PATH ...]

Each PATH is a ``.jsonl`` result file or a directory of them (default: ``results`` next to this
script). For every file (= one experiment) the script prints, per configuration, the median of
``seconds`` over the rounds for each build, and the speedup of the builds ``pr2`` and
``main-latest`` against the baseline ``pre-1551``. Speedup = median(pre-1551) / median(build), so
a value above 1 means the build is faster. Runs that recorded an error are left out and counted.
Needs only the standard library and numpy.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
BASE = "pre-1551"
COMPARED = ("pr2", "main-latest")

# Fields that define one configuration, per result file (file name without ".jsonl").
GROUPS: dict[str, tuple[str, ...]] = {
    "results": ("n", "threads", "extra"),
    "results_threads": ("n", "threads"),
    "results_reads": ("n", "threads", "output"),
    "results_extras_full": ("n", "threads", "extra_density"),
    "results_batch": ("n", "threads", "batch", "extra_density"),
}
# Other files: use those of these fields that vary within the file.
CANDIDATES = ("n", "threads", "batch", "scenarios", "output", "extra", "extra_density")
NOTES: dict[str, str] = {
    "results": "sweep: size x threads (extra = switched-off appliances, enabled at 10 %), 100 scenarios",
    "results_threads": "threads: 400 scenarios",
    "results_reads": "reads: output = what is read from the solution, 100 scenarios",
    "results_extras_full": "extras: extra_density = share of 2000 extra appliances enabled (none = no extras), 100 scenarios",
    "results_batch": "batch: scenarios per call, 4000 nodes, 1 thread",
}


def load_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read a JSON-lines file.

    Returns:
        The records without an ``error`` field, and the number of records that had one.
    """
    ok: list[dict[str, Any]] = []
    errors = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if "error" in rec:
                errors += 1
            else:
                ok.append(rec)
    return ok, errors


def group_fields(stem: str, records: list[dict[str, Any]]) -> list[str]:
    """Return the fields that define a configuration for the file ``stem``."""
    if stem in GROUPS:
        return list(GROUPS[stem])
    return [k for k in CANDIDATES if len({r.get(k) for r in records}) > 1]


def fmt(value: Any) -> str:
    """Format a configuration value for a table cell (``None`` becomes "none")."""
    return "none" if value is None else str(value)


def table(rows: list[list[str]]) -> list[str]:
    """Left-align the cells of ``rows`` (the first row is the header) into lines of text."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]


def summarise(path: Path) -> list[str]:
    """Return the text lines that summarise one result file."""
    records, errors = load_records(path)
    stem = path.stem
    head = f"== {path.name}: {len(records)} runs" + (f", {errors} failed runs left out" if errors else "")
    lines = [head + (f" -- {NOTES[stem]}" if stem in NOTES else "")]
    if not records:
        return lines
    fields = group_fields(stem, records)
    seconds: dict[tuple, dict[str, list[float]]] = {}  # configuration -> build -> seconds of every round
    for r in records:
        key = tuple(r.get(k) for k in fields)
        seconds.setdefault(key, {}).setdefault(r["label"], []).append(r["seconds"])
    found = {b for per in seconds.values() for b in per}
    builds = [b for b in (BASE, *COMPARED) if b in found] + sorted(found - {BASE, *COMPARED})

    rows = [[*fields, "runs/build", *[f"{b} [s]" for b in builds], *[f"{b} vs {BASE}" for b in COMPARED]]]
    for key, per in seconds.items():
        med = {b: float(np.median(v)) for b, v in per.items()}
        cells = [fmt(v) for v in key]
        cells.append("/".join(str(len(per.get(b, ()))) for b in builds))
        cells += [f"{med[b]:.4f}" if b in med else "-" for b in builds]
        cells += [f"{med[BASE] / med[b]:.2f}x" if BASE in med and b in med else "-" for b in COMPARED]
        rows.append(cells)
    return lines + table(rows)


def collect(paths: list[Path]) -> list[Path]:
    """Expand directories to their ``*.jsonl`` files (sorted by name) and keep files as given."""
    files: list[Path] = []
    for p in paths:
        files += sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    return files


def main(argv: list[str] | None = None) -> int:
    """Print the summary of every result file given on the command line (default: ``results/``)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, default=[HERE / "results"],
                    help="result files or directories (default: results/ next to this script)")
    files = collect(ap.parse_args(argv).paths)
    if not files:
        print("no .jsonl result files found", file=sys.stderr)
        return 1
    for i, f in enumerate(files):
        if i:
            print()
        print("\n".join(summarise(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
