"""Plot the extras and batch-size results as PNG figures.

Reads results/results_extras_full.jsonl and results/results_batch.jsonl and writes three figures to figures/:

- fig_extras_by_size.png: runtime per run by grid size, one thread, for each extras variant.
- fig_extras_by_threads.png: runtime per run by thread count, 4000 nodes, with and without 2000 switched-off appliances.
- fig_batch_size.png: runtime per scenario by batch size, 4000 nodes, one thread.

Each point is the median of the interleaved rounds. Requires matplotlib:

    py -3 plot_results.py
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results"
FIGURES = HERE / "figures"

BUILDS = ["pre-1551", "pr2", "main-latest"]
LABELS = {
    "pre-1551": "pre-1551 (before the fix)",
    "pr2": "pr2 (fix, PR #1551)",
    "main-latest": "main-latest (v1.13.214)",
}
STYLE = {"pre-1551": ("tab:red", "o"), "pr2": ("tab:blue", "s"), "main-latest": ("tab:green", "^")}
EXTRA_VARIANTS = [(None, "no extra appliances"), (0.0, "2000 appliances, all off"),
                  (0.1, "2000 appliances, 10 % on"), (1.0, "2000 appliances, 100 % on")]


def load(name: str) -> list[dict[str, Any]]:
    """Read one JSON-lines result file."""
    with (RESULTS / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def medians(records: list[dict[str, Any]], key: str, per_scenario: bool = False) -> dict[tuple[str, Any], float]:
    """Median seconds for each (build, key value); optionally divided by scenarios per run."""
    groups: dict[tuple[str, Any], list[float]] = defaultdict(list)
    for r in records:
        value = r["seconds"] / r["scenarios"] if per_scenario else r["seconds"]
        groups[(r["label"], r[key])].append(value)
    return {k: statistics.median(v) for k, v in groups.items()}


def plot_extras_by_size(extras: list[dict[str, Any]]) -> None:
    """One panel per grid size; x = extras variant; one thread."""
    sizes = sorted({r["n"] for r in extras})
    fig, axes = plt.subplots(1, len(sizes), figsize=(5 * len(sizes), 4.5), sharey=False)
    sub = [r for r in extras if r["threads"] == 1]
    for ax, n in zip(axes, sizes):
        rows = [r for r in sub if r["n"] == n]
        for build in BUILDS:
            values = []
            for density, _ in EXTRA_VARIANTS:
                vals = [r["seconds"] for r in rows if r["label"] == build and r["extra_density"] == density]
                values.append(statistics.median(vals) if vals else float("nan"))
            colour, marker = STYLE[build]
            ax.plot(range(len(EXTRA_VARIANTS)), values, marker=marker, color=colour, label=LABELS[build])
        ax.set_xticks(range(len(EXTRA_VARIANTS)))
        ax.set_xticklabels([label for _, label in EXTRA_VARIANTS], rotation=25, ha="right", fontsize=8)
        ax.set_title(f"{n} nodes, 1 thread")
        ax.set_ylabel("runtime per run [s]")
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle("Runtime of 100 scenarios by grid size and extra appliances (median of 3 rounds)")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_extras_by_size.png", dpi=150)
    plt.close(fig)


def plot_extras_by_threads(extras: list[dict[str, Any]]) -> None:
    """Two panels for 4000 nodes: without extras and with 2000 switched-off appliances; x = threads."""
    panels = [(None, "no extra appliances"), (0.0, "2000 switched-off appliances")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), sharey=True)
    for ax, (density, title) in zip(axes, panels):
        for build in BUILDS:
            pts = sorted(
                (r["threads"], r["seconds"]) for r in extras
                if r["n"] == 4000 and r["label"] == build and r["extra_density"] == density
            )
            threads: dict[int, list[float]] = defaultdict(list)
            for t, s in pts:
                threads[t].append(s)
            xs = sorted(threads)
            colour, marker = STYLE[build]
            ax.plot(xs, [statistics.median(threads[t]) for t in xs], marker=marker, color=colour, label=LABELS[build])
        ax.set_xscale("log", base=2)
        ax.set_xticks([1, 4, 8, 12])
        ax.set_xticklabels(["1", "4", "8", "12"])
        ax.set_xlabel("threads")
        ax.set_title(f"4000 nodes, 100 scenarios: {title}")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("runtime per run [s]")
    axes[0].set_ylim(bottom=0)
    axes[0].legend(fontsize=8)
    fig.suptitle("Runtime by thread count (median of 3 rounds)")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_extras_by_threads.png", dpi=150)
    plt.close(fig)


def plot_batch_size(batch: list[dict[str, Any]]) -> None:
    """Two panels: no extras and 2000 switched-off appliances; x = scenarios per call; y = time per scenario."""
    panels = [(None, "no extra appliances"), (0.0, "2000 switched-off appliances")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), sharey=True)
    for ax, (density, title) in zip(axes, panels):
        sub = [r for r in batch if r["extra_density"] == density]
        per = medians(sub, "batch", per_scenario=True)
        for build in BUILDS:
            sizes = sorted({b for (lab, b) in per if lab == build})
            colour, marker = STYLE[build]
            ax.plot(sizes, [per[(build, b)] for b in sizes], marker=marker, color=colour, label=LABELS[build])
        ax.set_xscale("log")
        ax.set_xticks([10, 100, 400, 1000])
        ax.set_xticklabels(["10", "100", "400", "1000"])
        ax.set_xlabel("scenarios per calculate call (batch size)")
        ax.set_title(f"4000 nodes, 1 thread: {title}")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("runtime per scenario [s]")
    axes[0].set_ylim(bottom=0)
    axes[0].legend(fontsize=8)
    fig.suptitle("Runtime per scenario by batch size (median of 3 rounds)")
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_batch_size.png", dpi=150)
    plt.close(fig)


def main() -> None:
    """Write all three figures."""
    FIGURES.mkdir(exist_ok=True)
    extras = load("results_extras_full.jsonl")
    batch = load("results_batch.jsonl")
    plot_extras_by_size(extras)
    plot_extras_by_threads(extras)
    plot_batch_size(batch)
    print("wrote", *sorted(p.name for p in FIGURES.glob("*.png")))


if __name__ == "__main__":
    main()
