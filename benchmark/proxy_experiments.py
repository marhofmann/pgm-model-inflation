"""Two Python-only experiments on the batch path: thread scaling of the per-thread copy, and the topology cache.

No C++ timers are needed. The experiments compare batch calls that differ in one thing only.

copy
    For each thread count T in 1, 4, 8, 12 the same pool of scenarios is run in two batch sizes:
    T scenarios (one per thread, so each thread does the same work) and all 48 pool scenarios.
    If the per-thread copy or restore grows with T, the one-scenario-per-thread time grows too,
    although every thread does the same solve.

topology
    One thread, 4000 nodes, 100 scenarios, three orders of the same topologies:
    ``same``: one topology repeated 100 times (the cache always hits);
    ``shuffled``: 10 topologies, each 10 times, in random order (the cache rarely hits);
    ``grouped``: the same 100 scenarios sorted by topology (the cache hits within each group).
    The number of topology changes between neighbouring scenarios is stored with each result.

Each timed call runs after the idle guard, in one process per build. Run from this folder:

    set PYTHONPATH=<pgm checkout>/src
    py -3 proxy_experiments.py copy --label pr2
    py -3 proxy_experiments.py topology --label pr2

Results are appended to ``results/proxy_copy.jsonl`` and ``results/proxy_topology.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import power_grid_model
from power_grid_model import CalculationMethod, PowerGridModel, initialize_array

import bench_inflated as bench
import idle_guard

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
THREADS = [1, 4, 8, 12]
POOL = 48  # scenarios in the pool; the copy experiment uses the first T or all of them
ROUNDS = 3


class Candidates(NamedTuple):
    """Candidate lines that a bit switches on. ``orig`` is the line it switches off, or -1."""

    ids: np.ndarray
    orig: np.ndarray


def candidates(par_ids: np.ndarray, rec_ids: np.ndarray, rec_orig: np.ndarray, chord_ids: np.ndarray) -> Candidates:
    """Concatenate the parallel lines, reconductoring alternatives and chord lines, one bit each."""
    ids = np.concatenate([par_ids, rec_ids, chord_ids])
    orig = np.concatenate([np.full(len(par_ids), -1), rec_orig, np.full(len(chord_ids), -1)])
    return Candidates(ids=ids, orig=orig)


def line_update(data: dict[str, Any], cand: Candidates, bits: np.ndarray) -> dict[str, Any]:
    """Batch update of the line statuses: row s switches on the candidates with ``bits[s, j] == 1``.

    Args:
        data: Input data from ``bench_inflated.build``.
        cand: The candidate lines.
        bits: 0/1 matrix of shape (scenarios, number of candidates).

    Returns:
        Update data with a ``line`` batch; the appliances keep their input status.
    """
    line = data["line"]
    upd = initialize_array("update", "line", (bits.shape[0], len(line)))
    upd["id"] = line["id"]
    upd["from_status"] = line["from_status"]
    upd["to_status"] = line["to_status"]
    pos = {int(i): j for j, i in enumerate(line["id"])}
    for j, lid in enumerate(cand.ids):
        on = bits[:, j] == 1
        k = pos[int(lid)]
        upd["from_status"][on, k] = 1
        upd["to_status"][on, k] = 1
        if cand.orig[j] >= 0:
            o = pos[int(cand.orig[j])]
            upd["from_status"][on, o] = 0
            upd["to_status"][on, o] = 0
    return {"line": upd}


def timed_call(model: PowerGridModel, upd: dict[str, Any], threads: int, tag: str) -> float:
    """Run one batch power flow after the idle guard and return its wall-clock time in seconds."""
    idle_guard.wait_idle(tag)
    start = time.perf_counter()
    model.calculate_power_flow(
        update_data=upd, calculation_method=CalculationMethod.newton_raphson, threading=threads
    )
    return time.perf_counter() - start


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append result rows as JSON lines."""
    RESULTS.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def cmd_copy(args: argparse.Namespace) -> None:
    """Thread scaling of the per-thread model copy: one scenario per thread, and the full pool."""
    rng = np.random.default_rng(args.seed)
    data, par, rec, rec_orig, chord, _extra = bench.build(args.n, args.extra, rng)
    cand = candidates(par, rec, rec_orig, chord)
    bits = (rng.random((POOL, len(cand.ids))) < args.density).astype(np.int8)
    model = PowerGridModel(data)
    version = f"{args.label} {power_grid_model.__version__}"

    rows: list[dict[str, Any]] = []
    for threads in THREADS:  # untimed warm-up of every combination
        for size in (threads, POOL):
            timed_call(model, line_update(data, cand, bits[:size]), threads, "warm-up")
    for rnd in range(1, ROUNDS + 1):
        for threads in THREADS:
            for size in (threads, POOL):
                upd = line_update(data, cand, bits[:size])
                seconds = timed_call(model, upd, threads, f"copy round {rnd} T={threads} S={size}")
                rows.append({"experiment": "copy", "label": args.label, "version": version, "n": args.n,
                             "extra": args.extra, "threads": threads, "scenarios": size, "round": rnd,
                             "seconds": seconds})
                print(rnd, threads, size, round(seconds, 4), flush=True)
    write(RESULTS / "proxy_copy.jsonl", rows)


def cmd_topology(args: argparse.Namespace) -> None:
    """Topology cache: the same 100 scenarios as one topology, shuffled, or grouped by topology."""
    rng = np.random.default_rng(args.seed)
    data, par, rec, rec_orig, chord, _extra = bench.build(args.n, args.extra, rng)
    cand = candidates(par, rec, rec_orig, chord)
    distinct = (rng.random((10, len(cand.ids))) < args.density).astype(np.int8)
    shuffled = np.repeat(distinct, 10, axis=0)  # 10 topologies, each 10 times
    shuffled = shuffled[np.random.default_rng(args.seed + 1).permutation(len(shuffled))]
    _, group = np.unique(shuffled, axis=0, return_inverse=True)
    grouped = shuffled[np.argsort(group.ravel(), kind="stable")]
    orders = {
        "same": np.repeat(distinct[:1], 100, axis=0),
        "shuffled": shuffled,
        "grouped": grouped,
    }
    model = PowerGridModel(data)
    version = f"{args.label} {power_grid_model.__version__}"

    runs: dict[str, list[float]] = {name: [] for name in orders}
    for name, bits in orders.items():  # warm-up
        timed_call(model, line_update(data, cand, bits), 1, f"warm-up {name}")
    for rnd in range(1, ROUNDS + 1):
        for name, bits in orders.items():
            seconds = timed_call(model, line_update(data, cand, bits), 1, f"topology round {rnd} {name}")
            runs[name].append(seconds)
            print(rnd, name, round(seconds, 4), flush=True)

    rows = []
    for name, bits in orders.items():
        changes = int(np.any(bits[1:] != bits[:-1], axis=1).sum())
        rows.append({"experiment": "topology", "label": args.label, "version": version, "n": args.n,
                     "extra": args.extra, "threads": 1, "order": name, "scenarios": len(bits),
                     "topology_changes": changes, "seconds_median": statistics.median(runs[name]),
                     "seconds_rounds": runs[name]})
    write(RESULTS / "proxy_topology.jsonl", rows)


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface with one subcommand per experiment."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="experiment", required=True)
    for name, fn in (("copy", cmd_copy), ("topology", cmd_topology)):
        p = sub.add_parser(name)
        p.add_argument("--label", required=True, help="build name, e.g. pr2")
        p.add_argument("--n", type=int, default=8000 if name == "copy" else 4000)
        p.add_argument("--extra", type=int, default=2000)
        p.add_argument("--density", type=float, default=0.1)
        p.add_argument("--seed", type=int, default=1)
        p.set_defaults(func=fn)
    return ap


def main() -> None:
    """Parse the arguments and run the chosen experiment."""
    args = build_parser().parse_args()
    random.seed(args.seed)
    args.func(args)


if __name__ == "__main__":
    main()
