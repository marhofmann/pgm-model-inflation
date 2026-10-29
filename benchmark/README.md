# Benchmark: commands and files

This file lists only the exact commands and the files. It does not contain the PGM source, a build or a wheel:
fetch and build PGM yourself.

## Requirements

- Python 3.10 or newer, `numpy`, `psutil`.
- One built PGM checkout per build (below).
- `bench_inflated.py` reads `peak_mb` from `psutil` `peak_wset`, which exists on Windows only.
- On Windows use `py -3` where this file says `python`.

## Build the three PGM checkouts

Clone the upstream repository once per build, check out the commit, and build it with the project's own build
instructions (see the build guide in the checkout). Each build needs its own directory, because the native
library is built inside the checkout.

| build name    | commit                                     | note                                         |
|---------------|--------------------------------------------|----------------------------------------------|
| `pre-1551`    | `3394561ef0d52caee0c2f149d925a15435e018f1` | v1.13.145, before the fix                    |
| `pr2`         | `c763f484616dd0686d586550ad7950ca2d771636` | the fix (PR #1551): one commit on top of `3394561ef` |
| `main-latest` | `4bd967f57683e14efa2547d08ee2403a63dbff1c` | v1.13.214                                    |

```
git clone https://github.com/PowerGridModel/power-grid-model.git pgm-pre-1551
git -C pgm-pre-1551 checkout 3394561ef0d52caee0c2f149d925a15435e018f1
git clone https://github.com/PowerGridModel/power-grid-model.git pgm-pr2
git -C pgm-pr2 checkout c763f484616dd0686d586550ad7950ca2d771636
git clone https://github.com/PowerGridModel/power-grid-model.git pgm-main-latest
git -C pgm-main-latest checkout 4bd967f57683e14efa2547d08ee2403a63dbff1c
```

`pr2` is one commit on top of `pre-1551`; if the clone does not contain it, fetch it from the branch that carries
the fix first. Then build each checkout. `PYTHONPATH` must point at the `src` directory of the checkout
(`pgm-pre-1551/src`, ...). The driver sets it for every run from `--builds NAME=SRC`; for `bench_inflated.py` run by
hand, set it yourself.

## Run the experiments

All commands run from this directory. Each run is a fresh subprocess; one JSON line per run is appended to
`runs/<file>` (change with `--out FILE`, number of interleaved rounds with `--rounds N`, default 3).
`--dry-run` lists the planned runs without running anything.

```
python run_experiments.py sweep   --builds pre-1551=../pgm-pre-1551/src pr2=../pgm-pr2/src main-latest=../pgm-main-latest/src
python run_experiments.py threads --builds pre-1551=../pgm-pre-1551/src pr2=../pgm-pr2/src main-latest=../pgm-main-latest/src
python run_experiments.py reads   --builds pre-1551=../pgm-pre-1551/src pr2=../pgm-pr2/src main-latest=../pgm-main-latest/src
python run_experiments.py extras  --builds pre-1551=../pgm-pre-1551/src pr2=../pgm-pr2/src main-latest=../pgm-main-latest/src
python run_experiments.py batch   --builds pre-1551=../pgm-pre-1551/src pr2=../pgm-pr2/src main-latest=../pgm-main-latest/src
```

| subcommand | grid                                                                                              | runs (3 rounds, 3 builds) | default output                |
|------------|---------------------------------------------------------------------------------------------------|---------------------------|-------------------------------|
| `sweep`    | 1000/2000/4000/8000 nodes x 1/4 threads; 4000 nodes x 2 threads; 4000 nodes x 1 thread + 2000 extra appliances; 100 scenarios | 90  | `runs/results.jsonl`          |
| `threads`  | 1000/4000/8000 nodes x 1/4/8/12 threads; 400 scenarios                                            | 108                       | `runs/results_threads.jsonl`  |
| `reads`    | 4000/8000 nodes x 1/4 threads x read level `node_u`/`node_line`/`node_line_all`/`all`; 100 scenarios | 144                    | `runs/results_reads.jsonl`    |
| `extras`   | 1000/4000/8000 nodes x 1/4/8/12 threads x {no extras, 2000 extra appliances 0/10/100 % enabled}; 100 scenarios | 432      | `runs/results_extras_full.jsonl` |
| `batch`    | 4000 nodes, 1 thread, batch size 10/100/400/1000 x {no extras, 2000 extra appliances all off}     | 72                        | `runs/results_batch.jsonl`    |

Idle guard: before each run the driver waits until the CPU load is below 10 % averaged over 3 s and logs each wait
to `logs/idle_log.txt`. It checks only before a run; it does not stop other work from starting during a run. Keep
the machine free while an experiment runs.

One run by hand (prints one JSON line):

```
PYTHONPATH=../pgm-pr2/src python bench_inflated.py --n 2000 --threads 4 --scenarios 200 --label pr2
```

In PowerShell set the variable first: `$env:PYTHONPATH = "..\pgm-pr2\src"`.

## Summarise

```
python summarize.py            # results/*.jsonl
python summarize.py runs       # your own runs
```

Prints, per experiment and configuration, the median `seconds` per build, and the speedup of `pr2` and
`main-latest` against `pre-1551` (median time of `pre-1551` divided by median time of the build).

## Copy and topology experiments (Python only)

These two experiments check the per-thread model copy and the topology cache. They use only the Python
interface, no C++ timers. One build at a time, in one process per call:

```
set PYTHONPATH=..\pgm-pr2\src
python proxy_experiments.py copy     --label pr2   # 8000 nodes, 2000 extra appliances, threads 1/4/8/12
python proxy_experiments.py topology --label pr2   # 4000 nodes, 2000 extra appliances, 1 thread
```

- `copy`: each thread count runs the same 48-scenario pool in two batch sizes. Size T gives one scenario per thread;
  size 48 gives the whole pool. Output: `results/proxy_copy.jsonl`, 3 rounds.
- `topology`: 100 scenarios as one topology (`same`), 10 topologies each repeated 10 times in random order
  (`shuffled`), or the same rows sorted by topology (`grouped`). Each result line records the number of topology
  changes between neighbouring scenarios. Output: `results/proxy_topology.jsonl`, 3 rounds.

Results (pr2 build, PGM 1.13 from the pr2 checkout; medians over the rounds, 1 run per round):

| experiment | setting | result |
|---|---|---|
| copy | one scenario per thread, T = 1 / 4 / 8 / 12 | 0.024–0.035 s / 0.037–0.046 s / 0.037–0.045 s / 0.047–0.050 s |
| copy | 48 scenarios, T = 1 / 4 / 8 / 12 | 0.88–0.97 s / 0.23–0.25 s / 0.13–0.15 s / 0.12–0.13 s |
| topology | `same`, 0 topology changes | 1.015 s |
| topology | `shuffled`, 86 topology changes | 0.905 s |
| topology | `grouped`, 9 topology changes | 0.797 s |

Reading: with one scenario per thread, the time per thread grows by about 50 % from 1 to 12 threads, while the
work per thread is the same. Part of that is the per-thread copy and restore; the C++ timers would separate them.
The topology order makes no clear difference at this size: `same` is the slowest, not the fastest, so the topology
rebuild is not what dominates here. The per-scenario update and restore of about 1200 candidate lines is the likely
cost; not measured. The three topology medians differ by about 25 %, with a spread of about 20 % between rounds,
so treat them as a weak result.

## Files

| file                                | content                                                                              |
|-------------------------------------|--------------------------------------------------------------------------------------|
| `bench_inflated.py`                 | one timed batch power flow on a synthetic inflated feeder; prints one JSON line      |
| `proxy_experiments.py`              | Python-only experiments `copy` and `topology` (see above)                            |
| `run_experiments.py`                | driver: subcommands `sweep`, `threads`, `reads`, `extras`, `batch`                   |
| `idle_guard.py`                     | waits for an idle machine before each run                                            |
| `summarize.py`                      | prints medians and speedups from the result files                                    |
| `results/results.jsonl`             | `sweep`, 90 runs                                                                     |
| `results/results_threads.jsonl`     | `threads`, 108 runs                                                                  |
| `results/results_reads.jsonl`       | `reads`, 144 runs                                                                    |
| `results/results_extras_full.jsonl` | `extras`, 432 runs                                                                   |
| `results/results_batch.jsonl`       | `batch`, 72 of 72 planned runs                                                       |
| `logs/`, `runs/`                    | created by the driver; not committed (`.gitignore`)                                  |

Each line of a result file is one run: the output of `bench_inflated.py` plus `round`. In the files in
`results/` the `version` field holds the build label only (local paths were removed). Runs made with the
current scripts write the build label and the PGM version string, for example `pr2 (PGM 1.13.145)`.
