"""One timed batch run on a synthetic inflated feeder; prints one JSON line.

Run it once per (build, size, threads) in a fresh process, so peak memory is per run:

    PYTHONPATH=<pgm-checkout>/src python bench_inflated.py --n 2000 --threads 4 --scenarios 200

The grid is a random radial feeder with n nodes and n loads. The inflated model adds disabled
candidates (parallel lines, reconductoring alternatives, chord lines) and, optionally, disabled
extra appliances. A scenario switches on a random subset of them by status only.

Notes:
- ``peak_mb`` is read from ``psutil`` ``peak_wset``, which exists on Windows only.
- ``version`` is the build label plus the version string of the imported ``power_grid_model``.
"""

import argparse
import json
import time

import numpy as np
import psutil
from power_grid_model import PowerGridModel, initialize_array
import power_grid_model

Arrays = dict[str, np.ndarray]


def pgm_version() -> str:
    """Return ``power_grid_model.__version__`` of the imported package, or "unknown" if it has none."""
    return str(getattr(power_grid_model, "__version__", "unknown"))


def build(
    n: int, extra_appliances: int, rng: np.random.Generator
) -> tuple[Arrays, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build the inflated feeder.

    Args:
        n: Number of nodes (and, minus the source node, of loads).
        extra_appliances: Number of extra switched-off ``sym_gen`` appliances (0 for none).
        rng: Random generator; the same seed gives the same grid.

    Returns:
        The input data (``node``, ``line``, ``sym_load``, ``source`` and optionally ``sym_gen``) and
        the ids of the parallel lines, the reconductoring alternatives, the line each alternative
        replaces, the chord lines and the extra appliances.
    """
    node = initialize_array("input", "node", n)
    node["id"] = np.arange(n)
    node["u_rated"] = 20e3

    parent = np.array([rng.integers(0, i) for i in range(1, n)])
    n_base = n - 1
    n_par, n_rec, n_chord = n // 10, n // 10, n // 10
    line = initialize_array("input", "line", n_base + n_par + n_rec + n_chord)
    line["id"] = n + np.arange(len(line))
    line["from_node"][:n_base] = parent
    line["to_node"][:n_base] = np.arange(1, n)
    line["r1"], line["x1"], line["c1"], line["tan1"], line["i_n"] = 0.15, 0.08, 1e-9, 0.0, 400.0
    line["from_status"] = line["to_status"] = 0
    line["from_status"][:n_base] = line["to_status"][:n_base] = 1

    k = n_base
    par_src = rng.choice(n_base, n_par, replace=False)
    line["from_node"][k : k + n_par] = line["from_node"][par_src]
    line["to_node"][k : k + n_par] = line["to_node"][par_src]
    par_ids = line["id"][k : k + n_par].copy()
    k += n_par
    rec_src = rng.choice(n_base, n_rec, replace=False)
    line["from_node"][k : k + n_rec] = line["from_node"][rec_src]
    line["to_node"][k : k + n_rec] = line["to_node"][rec_src]
    line["r1"][k : k + n_rec] *= 0.5
    rec_ids = line["id"][k : k + n_rec].copy()
    rec_orig = line["id"][rec_src].copy()
    k += n_rec
    a = rng.integers(0, n, n_chord)
    b = (a + rng.integers(5, 60, n_chord)) % n
    line["from_node"][k:] = a
    line["to_node"][k:] = b
    chord_ids = line["id"][k:].copy()

    load = initialize_array("input", "sym_load", n - 1)
    load["id"] = 10 * n + np.arange(n - 1)
    load["node"] = np.arange(1, n)
    load["status"] = 1
    load["type"] = 0
    load["p_specified"] = 10e3
    load["q_specified"] = 2e3

    source = initialize_array("input", "source", 1)
    source["id"] = 20 * n
    source["node"] = 0
    source["status"] = 1
    source["u_ref"] = 1.0
    source["sk"] = 1e12

    data = {"node": node, "line": line, "sym_load": load, "source": source}
    extra_ids = np.array([], dtype=line["id"].dtype)
    if extra_appliances:
        gen = initialize_array("input", "sym_gen", extra_appliances)
        gen["id"] = 30 * n + np.arange(extra_appliances)
        gen["node"] = rng.integers(1, n, extra_appliances)
        gen["status"] = 0
        gen["type"] = 0
        gen["p_specified"] = 20e3
        gen["q_specified"] = 0.0
        data["sym_gen"] = gen
        extra_ids = gen["id"].copy()
    return data, par_ids, rec_ids, rec_orig, chord_ids, extra_ids


def updates(
    data: Arrays,
    par_ids: np.ndarray,
    rec_ids: np.ndarray,
    rec_orig: np.ndarray,
    chord_ids: np.ndarray,
    extra_ids: np.ndarray,
    scenarios: int,
    density: float,
    rng: np.random.Generator,
    extra_density: float | None = None,
) -> Arrays:
    """Build the batch update data: each scenario switches on a random subset of the candidates.

    Args:
        data: Input data from :func:`build`.
        par_ids: Ids of the disabled parallel lines.
        rec_ids: Ids of the disabled reconductoring alternatives.
        rec_orig: Ids of the lines that the alternatives replace (switched off when one is on).
        chord_ids: Ids of the disabled chord lines.
        extra_ids: Ids of the disabled extra appliances (may be empty).
        scenarios: Number of scenarios in the batch.
        density: Probability that a line candidate is switched on in a scenario.
        rng: Random generator.
        extra_density: Probability that an extra appliance is switched on; ``None`` uses ``density``.

    Returns:
        Update arrays of shape ``(scenarios, n)`` for ``line`` and, if there are extra appliances,
        for ``sym_gen``.
    """
    line = data["line"]
    n_l = len(line)
    upd = initialize_array("update", "line", (scenarios, n_l))
    upd["id"] = line["id"]
    upd["from_status"] = line["from_status"]
    upd["to_status"] = line["to_status"]
    pos = {int(i): j for j, i in enumerate(line["id"])}

    def on(ids: np.ndarray, orig: np.ndarray | None = None) -> None:
        """Switch the lines ``ids`` on in a random subset of scenarios; switch the line ``orig[j]`` off with it."""
        bits = rng.random((scenarios, len(ids))) < density
        for j, lid in enumerate(ids):
            m = bits[:, j]
            upd["from_status"][m, pos[int(lid)]] = 1
            upd["to_status"][m, pos[int(lid)]] = 1
            if orig is not None:
                upd["from_status"][m, pos[int(orig[j])]] = 0
                upd["to_status"][m, pos[int(orig[j])]] = 0

    on(par_ids)
    on(rec_ids, rec_orig)
    on(chord_ids)
    out = {"line": upd}
    if len(extra_ids):
        g = initialize_array("update", "sym_gen", (scenarios, len(extra_ids)))
        g["id"] = extra_ids
        g["status"] = rng.random((scenarios, len(extra_ids))) < (density if extra_density is None else extra_density)
        out["sym_gen"] = g
    return out


def main() -> None:
    """Parse the command line, run one timed batch power flow and print one JSON line with the result."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--scenarios", type=int, default=200)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--extra-appliances", type=int, default=0)
    ap.add_argument("--density", type=float, default=0.1)
    ap.add_argument("--extra-density", type=float, default=None)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--output", choices=["all", "node_line_all", "node_line", "node_u"], default="all")
    a = ap.parse_args()
    # what the caller actually reads from the solution; "all" = every attribute of every component (the default)
    OUT = {
        "all": None,
        "node_line_all": ["node", "line"],
        "node_line": {"node": ["u_pu"], "line": ["loading"]},
        "node_u": {"node": ["u_pu"]},
    }[a.output]

    rng = np.random.default_rng(a.seed)
    data, *ids = build(a.n, a.extra_appliances, rng)
    upd = updates(data, *ids, a.scenarios, a.density, rng, a.extra_density)
    model = PowerGridModel(data)
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    res = model.calculate_power_flow(
        update_data=upd, threading=a.threads, continue_on_batch_error=True,
        output_component_types=OUT,
    )
    dt = time.perf_counter() - t0
    failed = len(model.batch_error.failed_scenarios) if model.batch_error is not None else 0
    if failed:
        raise SystemExit(f"{failed} of {a.scenarios} scenarios failed to converge; timing would be meaningless")
    peak = proc.memory_info().peak_wset
    values_read = sum(
        sum(x.size for x in v.values()) if isinstance(v, dict) else v.size * len(v.dtype.names) for v in res.values()
    )
    print(json.dumps({
        "values_per_scenario": values_read // a.scenarios,
        "label": a.label, "output": a.output, "version": f"{a.label} (PGM {pgm_version()})",
        "n": a.n, "extra": a.extra_appliances, "extra_density": a.extra_density, "scenarios": a.scenarios, "threads": a.threads,
        "seconds": round(dt, 4), "peak_mb": round(peak / 2**20, 1), "rss_before_mb": round(rss0 / 2**20, 1),
        "u_min_mean": float(np.nanmean(res["node"]["u_pu"].min(axis=1))),
    }))


if __name__ == "__main__":
    main()
