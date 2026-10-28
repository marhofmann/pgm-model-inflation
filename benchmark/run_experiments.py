"""Driver for all benchmark experiments: one subcommand per experiment, one fresh process per run.

    python run_experiments.py <experiment> --builds NAME=SRC [NAME=SRC ...] [--rounds N] [--out FILE]

Experiments (the parameter grid of each is fixed in this file):

    sweep    sizes 1000/2000/4000/8000 x threads 1/4, plus 4000 nodes x 2 threads and 4000 nodes x
             1 thread with 2000 extra appliances; 100 scenarios
    threads  sizes 1000/4000/8000 x threads 1/4/8/12; 400 scenarios
    reads    sizes 4000/8000 x threads 1/4 x four read levels (what is read from the solution);
             100 scenarios
    extras   sizes 1000/4000/8000 x threads 1/4/8/12 x {no extra appliances, 2000 extra appliances
             that are 0 / 10 / 100 % enabled}; 100 scenarios
    batch    4000 nodes, 1 thread, batch sizes 10/100/400/1000 x {no extra appliances, 2000 extra
             appliances all off}

Every experiment runs each configuration once per build and repeats that for ``--rounds`` rounds
(default 3). The rounds are interleaved (all configurations and builds once, then again), so slow
drift hits every build equally. Each run is ``bench_inflated.py`` in a fresh subprocess, so peak
memory is per run, and one JSON line per run is appended to the output file.

Builds: ``NAME=SRC`` where SRC is the ``src`` directory of a PGM checkout that you built yourself.
It becomes ``PYTHONPATH`` of the child process, NAME becomes the ``label`` of its records. The
order of the builds is the order in which they run within one configuration. The summary script
expects the names ``pre-1551``, ``pr2`` and ``main-latest``.

Idle guard: before each run the driver waits until the CPU load is below 10 % averaged over 3 s
(see ``idle_guard.py``). This checks only BEFORE a run. It does not stop other work from starting
while a run is going, so keep the machine free for the whole experiment.
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import idle_guard

HERE = Path(__file__).resolve().parent
BENCH = HERE / "bench_inflated.py"
RUNS = HERE / "runs"  # default output directory (not committed)
ROUNDS = 3
EXTRA_APPLIANCES = 2000  # number of switched-off appliances in the "extras" and "batch" variants


@dataclass
class Job:
    """One configuration of an experiment, run once per build and round."""

    tags: dict[str, Any]  # configuration fields stored in every record of this job
    args: list[str]  # command-line arguments for bench_inflated.py (the label is added per build)


@dataclass
class Experiment:
    """An experiment: its name, what it measures, its default output file and its parameter grid."""

    name: str
    summary: str
    out_name: str
    jobs: Callable[[], list[Job]]


def bench_args(
    n: int,
    scenarios: int,
    threads: int,
    extra: int | None = None,
    extra_density: float | None = None,
    output: str | None = None,
) -> list[str]:
    """Return the ``bench_inflated.py`` arguments for one configuration (options left at ``None`` are omitted)."""
    args = ["--n", str(n), "--scenarios", str(scenarios), "--threads", str(threads)]
    if extra is not None:
        args += ["--extra-appliances", str(extra)]
    if extra_density is not None:
        args += ["--extra-density", str(extra_density)]
    if output is not None:
        args += ["--output", output]
    return args


def sweep_jobs() -> list[Job]:
    """Grid of ``sweep``: 100 scenarios, extra appliances (if any) enabled at the default 10 %."""
    configs = [(n, t, 0) for n in (1000, 2000, 4000, 8000) for t in (1, 4)] + [(4000, 2, 0), (4000, 1, 2000)]
    return [Job({"n": n, "threads": t, "extra": e}, bench_args(n, 100, t, extra=e)) for n, t, e in configs]


def threads_jobs() -> list[Job]:
    """Grid of ``threads``: 400 scenarios, no extra appliances."""
    return [Job({"n": n, "threads": t}, bench_args(n, 400, t)) for n in (1000, 4000, 8000) for t in (1, 4, 8, 12)]


def reads_jobs() -> list[Job]:
    """Grid of ``reads``: 100 scenarios; the read level is the ``--output`` option of ``bench_inflated.py``."""
    levels = ["node_u", "node_line", "node_line_all", "all"]
    return [
        Job({"n": n, "threads": t, "output": o}, bench_args(n, 100, t, output=o))
        for n in (4000, 8000)
        for t in (1, 4)
        for o in levels
    ]


def extras_jobs() -> list[Job]:
    """Grid of ``extras``: 100 scenarios; density ``None`` means no extra appliances at all."""
    jobs = []
    for n in (1000, 4000, 8000):
        for t in (1, 4, 8, 12):
            for v in (None, 0.0, 0.1, 1.0):
                extra = None if v is None else EXTRA_APPLIANCES
                jobs.append(Job({"n": n, "threads": t, "extra_density": v},
                                bench_args(n, 100, t, extra=extra, extra_density=v)))
    return jobs


def batch_jobs() -> list[Job]:
    """Grid of ``batch``: 4000 nodes, 1 thread; the batch size is the number of scenarios per call."""
    jobs = []
    for batch in (10, 100, 400, 1000):
        for v in (None, 0.0):
            extra = None if v is None else EXTRA_APPLIANCES
            jobs.append(Job({"n": 4000, "threads": 1, "batch": batch, "extra_density": v},
                            bench_args(4000, batch, 1, extra=extra, extra_density=v)))
    return jobs


EXPERIMENTS: dict[str, Experiment] = {
    e.name: e
    for e in (
        Experiment("sweep", "runtime vs grid size and threads, with and without extra appliances",
                   "results.jsonl", sweep_jobs),
        Experiment("threads", "thread scaling for 1000/4000/8000 nodes, 400 scenarios",
                   "results_threads.jsonl", threads_jobs),
        Experiment("reads", "runtime vs what is read from the solution",
                   "results_reads.jsonl", reads_jobs),
        Experiment("extras", "switched-off appliances over grid sizes and thread counts",
                   "results_extras_full.jsonl", extras_jobs),
        Experiment("batch", "runtime vs batch size at 4000 nodes, 1 thread",
                   "results_batch.jsonl", batch_jobs),
    )
}


def parse_builds(specs: Sequence[str], check_paths: bool = True) -> dict[str, Path]:
    """Parse ``NAME=SRC`` specifications into an ordered ``{name: src directory}`` mapping.

    Args:
        specs: Strings of the form ``NAME=SRC``; SRC is the ``src`` directory of a built PGM checkout.
        check_paths: If true, require that SRC is a directory containing the ``power_grid_model`` package.

    Returns:
        The builds in the order given, with absolute paths.

    Raises:
        SystemExit: On a malformed or duplicate specification, or (with ``check_paths``) a bad SRC.
    """
    builds: dict[str, Path] = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            raise SystemExit(f"bad build '{spec}': expected NAME=SRC, e.g. pr2=../pgm-pr2/src")
        if name in builds:
            raise SystemExit(f"build name '{name}' given twice")
        src = Path(path).expanduser().resolve()
        if check_paths and not (src / "power_grid_model").is_dir():
            raise SystemExit(f"build '{name}': {src} is not the src directory of a PGM checkout "
                             "(it must contain the power_grid_model package)")
        builds[name] = src
    return builds


def plan(exp: Experiment, builds: Sequence[str], rounds: int) -> Iterator[tuple[int, Job, str]]:
    """Yield ``(round, job, build)`` in run order: rounds outermost, then configurations, then builds."""
    jobs = exp.jobs()
    for rnd in range(rounds):
        for job in jobs:
            for build in builds:
                yield rnd, job, build


def scrub(text: str, *paths: Path) -> str:
    """Replace the given local paths in ``text`` by placeholders, so error records do not carry them."""
    for path, placeholder in zip(paths, ("<src>", "<benchmark>")):
        text = text.replace(str(path), placeholder)
    return text


def run_one(build: str, src: Path, job: Job) -> dict[str, Any]:
    """Run ``bench_inflated.py`` once in a fresh subprocess.

    Args:
        build: Build name, passed as ``--label``.
        src: ``src`` directory of the PGM checkout; becomes ``PYTHONPATH`` of the child.
        job: The configuration to run.

    Returns:
        The JSON record printed by the benchmark, or ``{"error": <last 300 characters of its output>}``
        if the process failed.
    """
    env = dict(os.environ, PYTHONPATH=str(src), OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    cmd = [sys.executable, str(BENCH), *job.args, "--label", build]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if p.returncode:
        return {"error": scrub((p.stderr or p.stdout)[-300:], src, HERE)}
    return json.loads(p.stdout.strip().splitlines()[-1])


def run_experiment(exp: Experiment, builds: dict[str, Path], rounds: int, out: Path) -> None:
    """Run all runs of ``exp`` (waiting for an idle machine before each) and append one JSON line per run to ``out``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    for rnd, job, build in plan(exp, list(builds), rounds):
        idle_guard.wait_idle(f"[{exp.name} round {rnd} {build} {job.tags}]")
        result = run_one(build, builds[build], job)
        rec = {"label": build, **job.tags, "round": rnd} | result
        with out.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")
        print(rnd, build, job.tags, rec.get("seconds", rec.get("error")), flush=True)


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser with one subcommand per experiment."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="experiment", required=True, metavar="experiment")
    for exp in EXPERIMENTS.values():
        p = sub.add_parser(exp.name, help=exp.summary, description=exp.summary)
        p.add_argument("--builds", "--build", nargs="+", action="extend", required=True, metavar="NAME=SRC",
                       help="builds to compare: NAME=SRC, SRC = src directory of a built PGM checkout "
                            "(may be repeated)")
        p.add_argument("--rounds", type=int, default=ROUNDS, help=f"interleaved rounds (default {ROUNDS})")
        p.add_argument("--out", type=Path, default=None,
                       help=f"JSON-lines file to append to (default: runs/{exp.out_name} next to this script)")
        p.add_argument("--dry-run", action="store_true",
                       help="print the planned runs and their number, run nothing (build paths are not checked)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run (or, with ``--dry-run``, list) the chosen experiment."""
    args = build_parser().parse_args(argv)
    if args.rounds < 1:
        raise SystemExit("--rounds must be at least 1")
    exp = EXPERIMENTS[args.experiment]
    builds = parse_builds(args.builds, check_paths=not args.dry_run)
    out = args.out if args.out is not None else RUNS / exp.out_name
    if args.dry_run:
        runs = list(plan(exp, list(builds), args.rounds))
        for rnd, job, build in runs:
            print(rnd, build, job.tags, " ".join(job.args))
        print(f"{len(runs)} runs = {args.rounds} rounds x {len(exp.jobs())} configurations x {len(builds)} builds; "
              f"would append to {out}")
        return 0
    run_experiment(exp, builds, args.rounds, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
