"""Tests for the inflated six-bus ring model: batch shape, base-grid equivalence, limits and bound bits."""

import numpy as np
import pytest
from power_grid_model import CalculationMethod, PowerGridModel

from inflated_model.ring import (
    BOUND,
    LOADING_LIMIT,
    MEASURE_TABLE,
    U_MIN_LIMIT,
    feasible,
    load_base,
    run,
    score,
    status_updates,
)

N_BITS = len(MEASURE_TABLE)
GOOD_DESIGN = [1, 1, 1, 0, 0, 0, 1, 0]


@pytest.fixture(scope="module")
def batch() -> tuple:
    """Run the full 2^8 batch once for all tests."""
    return run()


def row_of(solutions: np.ndarray, design: list[int]) -> int:
    """Index of a design in the solution matrix."""
    return int(np.flatnonzero((solutions == np.array(design)).all(axis=1))[0])


def test_run_returns_256_binary_rows_of_width_8(batch: tuple) -> None:
    solutions = batch[2]
    assert N_BITS == 8
    assert solutions.shape == (256, N_BITS)
    assert set(np.unique(solutions)) <= {0, 1}


def test_first_row_is_all_zeros(batch: tuple) -> None:
    assert not batch[2][0].any()


def test_feasible_gives_128_true(batch: tuple) -> None:
    assert feasible(batch[2]).sum() == 128


def test_all_off_row_equals_base_grid_power_flow(batch: tuple) -> None:
    result = batch[3]
    base = load_base()
    base_result = PowerGridModel(base).calculate_power_flow(calculation_method=CalculationMethod.newton_raphson)
    n_base = len(base["line"])
    np.testing.assert_allclose(result["line"]["loading"][0, :n_base], base_result["line"]["loading"], atol=1e-9)


def test_good_design_meets_voltage_limit(batch: tuple) -> None:
    solutions, result = batch[2], batch[3]
    i = row_of(solutions, GOOD_DESIGN)
    assert feasible(solutions)[i]
    u_min = score(solutions, result)[0]
    assert u_min[i] >= U_MIN_LIMIT


def test_good_design_meets_loading_limit(batch: tuple) -> None:
    solutions, result = batch[2], batch[3]
    i = row_of(solutions, GOOD_DESIGN)
    loading = score(solutions, result)[1]
    assert loading[i] <= LOADING_LIMIT


def test_all_off_status_updates_keep_base_status(batch: tuple) -> None:
    data, ids = batch[0], batch[1]
    upd = status_updates(data, ids, np.zeros((1, N_BITS), dtype=int))
    for comp in ("line", "transformer"):
        for field in ("from_status", "to_status"):
            np.testing.assert_array_equal(upd[comp][field][0], data[comp][field])


def test_bound_bits_are_equal_in_every_feasible_design(batch: tuple) -> None:
    s = batch[2][feasible(batch[2])]
    assert (s[:, BOUND[0]] == s[:, BOUND[1]]).all()
    assert BOUND == (3, 4)
