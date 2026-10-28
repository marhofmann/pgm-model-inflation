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

## Files

| file                                | content                                                                              |
|-------------------------------------|--------------------------------------------------------------------------------------|
| `bench_inflated.py`                 | one timed batch power flow on a synthetic inflated feeder; prints one JSON line      |
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
