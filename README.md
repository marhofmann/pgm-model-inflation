# pgm-model-inflation

Model inflation for [power-grid-model](https://github.com/PowerGridModel/power-grid-model) (PGM).

This repository holds a small worked example and a benchmark. It does not contain any PGM code, builds or wheels.

## The idea

Grid planning asks the same question many times: which set of measures makes the grid good enough, at what cost?
A measure can be a parallel line, a better conductor, a new line, a larger transformer or a changed open point.
Every combination of measures is one design. The number of designs grows fast.

Model inflation keeps the work for one design small.

1. Build one model that holds the base grid.
2. Add every candidate measure to the same model, as an element that is switched off.
3. Describe a design as a 0/1 vector. One bit per measure. The all-zero vector is the base grid.
4. Turn a batch of design vectors into one batch update. The update only changes the status of elements.
5. Call the batch power flow once. PGM returns one result per design.

Why this helps planning:

- The model is built and validated once, not once per design.
- A status change is a small update. The grid is not rebuilt for each design.
- PGM can reuse work between scenarios and can run scenarios on several threads.
- An optimiser, or a plain enumeration, gets many designs evaluated per call.

Some bits depend on each other. For example, a new line needs its open point, or the grid gets a closed cycle or an island.
The example handles this by marking such designs infeasible in the score. Other models need their own rules.

## Quick start: the ring example

The example is in `src/inflated_model/ring.py`. It uses the six-bus medium-voltage open ring from pandapower
(`simple_mv_open_ring_net`). pandapower and power-grid-model-io convert it to a PGM input.
Loads are scaled by 6.0 so that the measures matter.

Install:

```
pip install -e .
```

Run:

```
python -m inflated_model.ring
```

On Windows with the Microsoft Store stub, use `py -3 -m inflated_model.ring`.

### What it prints

The script evaluates all 2^8 = 256 design vectors with one model and one batch call. It prints:

- the number of solution vectors, with the text "1 inflated model, 1 batch call";
- the number of elements per component type in the inflated model;
- the list of measure names, one per bit;
- the five designs with the lowest penalised objective. For each design it prints the bit vector, the minimum
  node voltage in per unit, the maximum loading of lines and transformers, the line losses in kW and the objective.

The objective in the example is a demo. It is a sum of measure costs, a loss term and penalties for low voltage
and for loading above 0.8. The costs are placeholders. Designs with a bound pair of bits that disagree get an infinite objective.

### The 8 bits

The bit order is the order of the table in the docstring of `ring.py`. The table there is the reference.

| Bit | Measure | Bit = 1 |
| --- | --- | --- |
| 0 | Parallel line 1-2 | copy of line 1-2 switched on next to the original |
| 1 | Parallel line 6-1 | copy of line 6-1 switched on next to the original |
| 2 | Reconductoring 2-3 | alternative line 2-3 (lower impedance, higher rating) on; original off |
| 3 | New line 2-5 | chord between bus 2 and bus 5 switched on (closes the cycle 2-1-6-5) |
| 4 | Open point 5-6 (new line) | ring line 5-6 opened at one end; bound to bit 3 |
| 5 | Transformer swap | alternative transformer (1.5x rating) on; original off |
| 6 | Transformer parallel | second 25 MVA unit on next to the original |
| 7 | Move open point | open line 3-4, close line 4-5 (the former open point) |

Bits 3 and 4 are bound: both on or both off.

### What is guaranteed

The example runs, and that is all. It shows the mechanism. It is not a planning tool.
The grid, the costs and the limits are illustrative. Nothing here is validated for real planning work.

## How the problem was found

The first clue was not a profile. It was a surprise in the timings.

An inflated model holds many appliances that are switched off. A switched-off appliance should cost almost nothing.
The batch runs were slower with 2000 switched-off appliances than without them, and the gap grew with the grid size.

The medians of the raw runs (one thread, 100 scenarios, 3 rounds) show this:

| Feeder size | Build | No extra appliances | 2000 switched off | Increase |
| --- | --- | --- | --- | --- |
| 4000 nodes | pre-1551 (before the fix) | 1.77 s | 2.81 s | +58 % |
| 4000 nodes | pr2 (the fix) | 0.87 s | 0.90 s | +4 % |
| 4000 nodes | main-latest | 0.86 s | 0.91 s | +6 % |
| 8000 nodes | pre-1551 (before the fix) | 5.56 s | 8.03 s | +44 % |
| 8000 nodes | pr2 (the fix) | 1.97 s | 1.99 s | +1 % |
| 8000 nodes | main-latest | 1.93 s | 1.94 s | +0.5 % |

That surprise led to a profile of one batch. The profile showed that most of the time went into building the result
for each appliance, not into the power flow. Tracing that step led to a copy of the whole output vector for every
appliance, in `get_component_output`. PR #1551 removes the copy, so the cost of a switched-off appliance drops to
almost nothing.

The benchmark keeps the switched-off appliances as a separate factor because that case exposed the problem.

## Benchmark

### What it measures

The benchmark times one batch power-flow call on a synthetic inflated feeder. The script is `bench_inflated.py`
(see `benchmark/README.md` for where it lives in this repository [unverified: location in this repository]).
Each run is a fresh process, so peak memory is per run.

The grid is a random radial feeder with n nodes and n-1 loads. The inflated model adds disabled candidates:
parallel lines, reconductoring alternatives and chord lines, each n/10 pieces.
Optionally it adds disabled extra appliances (generators). Each scenario switches on a random subset of the
candidates by status only.

The study compares three PGM builds:

- pre-1551, before the fix;
- pr2, with the fix (PR #1551);
- main-latest, a later main commit.

Factors in the main result file (`results_extras_full.jsonl`, 432 runs):

| Factor | Values |
| --- | --- |
| Build | pre-1551, pr2, main-latest |
| Feeder size n | 1000, 4000, 8000 nodes |
| Threads | 1, 4, 8, 12 |
| Disabled extra appliances | none, or 2000 with switch-on density 0.0, 0.1, 1.0 |
| Scenarios per call | 100 |
| Rounds | 3 per cell |

Each run records the time of the batch call in seconds, the peak working set in MB, and the mean of the minimum
node voltage as a sanity value. A run stops with an error if any scenario fails to converge, so no timing comes from a failed batch.

### Where the results are

The results are in `benchmark/results`. The raw data is JSON Lines, one run per line.

### Key result

Medians over the 3 rounds of one cell, in seconds, read from `results_extras_full.jsonl`.
Feeder of 8000 nodes, 100 scenarios per call.

| Cell | pre-1551 | pr2 | main-latest |
| --- | --- | --- | --- |
| 1 thread, no extra appliances | 5.56 | 1.97 | 1.93 |
| 12 threads, 2000 disabled extra appliances, density 0.1 | 2.77 | 0.39 | 0.35 |

For the second row, the ratio pre-1551 to pr2 is about 7. For the first row it is about 2.8.
The ratios are computed from the medians above.

Read this with care:

- The numbers come from one machine. See the caveats.
- They are only as reliable as the idle guard allows.
- Other cells give other ratios. The full table is in the result file.
- The numbers show a difference between builds on this synthetic feeder. They are not a general claim about PGM.

## Reproduce the benchmark on another machine

This repository does not provide PGM source, builds or wheels. You fetch and build the PGM versions yourself.

1. Clone the PGM repository: https://github.com/PowerGridModel/power-grid-model
2. Check out three commits, one per build (a separate working tree for each is convenient):

   | Name | Commit | Note |
   | --- | --- | --- |
   | pre-1551 | `3394561ef0d52caee0c2f149d925a15435e018f1` | v1.13.145, before the fix |
   | pr2 | `c763f484616dd0686d586550ad7950ca2d771636` | the fix, PR #1551 |
   | main-latest | `4bd967f57683e14efa2547d08ee2403a63dbff1c` | v1.13.214 |

3. Build each one with the PGM project's own build instructions. Use the same compiler, the same build type and the
   same Python for all three. Follow the PGM documentation for the details.
4. Install the benchmark extra of this repository (`pip install -e ".[benchmark]"`), which adds `psutil`.
5. Run the benchmark with `--build NAME=PATH` once per build. `PATH` is the `src` directory of that build,
   the one that contains `power_grid_model`. For example, `--build pr2=<path to the pr2 checkout>/src`.

The exact commands and options are in `benchmark/README.md`. Use them as written there.

Before you start:

- Close other programs. The timings are meaningless on a busy machine.
- Use the same thread counts if you want to compare with the result files.
- Each run is one fresh process. Do not merge runs into one process.

## Caveats

- One machine: Windows 11 with 6 performance and 8 efficiency cores. Thread scaling and the 12-thread results
  depend on this layout. Other CPUs may differ.
- The idle guard checks the CPU load only before each run. It does not check during a run. A load that starts
  later can still disturb a timing.
- `results_batch.jsonl` is missing 2 of 72 runs.
- A pipeline experiment exists in the working files. It is not included in this repository.
- Three rounds per cell is a small sample. Medians of 3 are rough.
- The feeder is synthetic and random. It is not a real grid.
- The numbers are not a general claim about PGM performance.
- The benchmark compares builds before and after one fix. The mathematics of that fix is not in this README.
  It belongs to the PGM repository.

## Requirements

Python 3.10 or newer. numpy, pandapower (3.0 or newer), power-grid-model (1.13 or newer) and power-grid-model-io
(2.0 or newer). The benchmark also needs psutil. These are the pins in `pyproject.toml`.

Note that `pip install -e .` installs a released PGM from PyPI. The benchmark uses your own builds instead.

## License

MIT License. See `LICENSE`.
