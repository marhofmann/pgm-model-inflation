"""Model inflation for power-grid-model, shown on a six-bus MV open ring.

Model inflation evaluates many grid designs with one power-grid-model and one batch call:

1. Inflate: one model holds the base grid plus every candidate measure as a disabled element
   (both ends switched off).
2. Encode: a solution is a fixed-length 0/1 vector with one bit per measure. The all-off vector
   is the base grid.
3. Batch: a batch of solutions becomes one power-grid-model batch update that only changes
   element status (``from_status`` and ``to_status``). One ``calculate_power_flow`` call then
   solves every design.

The grid is pandapower's ``simple_mv_open_ring_net``. A 25 MVA transformer feeds bus 1 from the
110 kV source at node 0. Lines 1-2, 2-3, 3-4, 4-5, 5-6 and 6-1 form a 20 kV ring that is normally
open at line 4-5. Bus numbers are power-grid-model node ids.

    bit  measure                      bit = 1
    0    Parallel line 1-2            copy of line 1-2 switched on next to the original
    1    Parallel line 6-1            copy of line 6-1 switched on next to the original
    2    Reconductoring 2-3           alternative line 2-3 (lower impedance, higher rating) on; original off
    3    New line 2-5                 new line between bus 2 and bus 5 on (closes the cycle 2-1-6-5)
    4    Open point 5-6 (new line)    ring line 5-6 opened at one end; bound to bit 3
    5    Transformer swap             alternative transformer (1.5x rating) on; original off
    6    Transformer parallel         second 25 MVA unit on next to the original
    7    Move open point              open line 3-4, close line 4-5 (the former open point)

Run it with ``py -3 ring.py`` or ``python -m inflated_model.ring``; it prints the five best designs.
"""

import itertools
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple, TypeAlias

import numpy as np
import numpy.typing as npt
import pandapower.networks as pn
from power_grid_model import CalculationMethod, CalculationType, PowerGridModel, initialize_array
from power_grid_model.validation import assert_valid_input_data
from power_grid_model_io.converters import PandaPowerConverter

ComponentArray: TypeAlias = npt.NDArray[np.void]  # structured array, one record per element (batch: scenario x element)
Dataset: TypeAlias = dict[str, ComponentArray]  # power-grid-model data keyed by component name, such as "line"
BoolVector: TypeAlias = npt.NDArray[np.bool_]  # one entry per design
FloatVector: TypeAlias = npt.NDArray[np.float64]  # one entry per design

# One row per measure, in bit order: (name, measure type, cost in k EUR for the demo objective).
# Measure types: 0 parallel line, 1 reconductoring, 2 new line (its open point shares type 2), 3 transformer,
# 4 topology. No code reads the type; it only describes the measure.
MEASURE_TABLE: list[tuple[str, int, float]] = [
    ("Parallel line 1-2", 0, 40.0),
    ("Parallel line 6-1", 0, 40.0),
    ("Reconductoring 2-3", 1, 60.0),
    ("New line 2-5", 2, 90.0),
    ("Open point 5-6 (new line)", 2, 5.0),
    ("Transformer swap", 3, 120.0),
    ("Transformer parallel", 3, 100.0),
    ("Move open point", 4, 5.0),
]
MEASURES = [name for name, _type, _cost in MEASURE_TABLE]
COST: FloatVector = np.array([cost for _name, _type, cost in MEASURE_TABLE])
BOUND = (3, 4)  # bits that must be equal: new line 2-5 and its open point 5-6 (otherwise a cycle or an island)
LOAD_SCALE = 6.0  # factor on the active and reactive power of every load, to stress the ring so the measures matter
LOADING_LIMIT = 0.8  # highest allowed loading of every line and transformer [-]
U_MIN_LIMIT = 0.95  # lowest allowed node voltage [p.u.]

ON = 1  # status of a connected element end
OFF = 0  # status of a disconnected element end
BOTH_ENDS = ("from_status", "to_status")  # status attributes of a line or transformer
FROM_END = ("from_status",)  # status attribute of the from-end only

# Row of each ring line in the converted input data (pandapower's line order).
LINE_12, LINE_23, LINE_34, LINE_45, LINE_56, LINE_61 = range(6)
TRAFO = 0  # row of the only transformer (node 0 to bus 1)

# Attributes that a candidate element scales, relative to the element it copies (demo values).
RECONDUCTOR_FACTORS = {"r1": 0.5, "x1": 0.9, "i_n": 1.5}  # line 2-3: lower impedance, 1.5x rated current
NEW_LINE_FACTORS = {"r1": 1.5, "x1": 1.5}  # line 2-5: the type of line 1-2 with 1.5x its impedance
TRAFO_SWAP_FACTORS = {"sn": 1.5, "pk": 0.8}  # transformer: 1.5x rated power, 0.8x short-circuit loss

VIOLATION_PENALTY = 500.0  # penalty per p.u. of voltage below U_MIN_LIMIT and per unit of loading above LOADING_LIMIT
PENALTY_WEIGHT = 100.0  # k EUR per unit of penalty in the objective
LOSS_COST = 8.0  # k EUR per kW of line losses in the objective
W_PER_KW = 1e3  # watts per kilowatt
N_BEST = 5  # number of designs that main() prints


@dataclass(frozen=True)
class ElementIds:
    """Ids of the elements that the measures switch, in bit order."""

    parallel_12: int  # bit 0: copy of line 1-2
    parallel_61: int  # bit 1: copy of line 6-1
    line_23: int  # bit 2: original line 2-3, switched off
    recond_23: int  # bit 2: reconductored line 2-3, switched on
    new_line_25: int  # bit 3: new line 2-5
    line_56: int  # bit 4: ring line 5-6, opened at its from-end
    trafo: int  # bit 5: original transformer, switched off
    trafo_swap: int  # bit 5: larger transformer, switched on
    trafo_parallel: int  # bit 6: second transformer
    line_34: int  # bit 7: ring line 3-4, opened
    line_45: int  # bit 7: ring line 4-5 at the normally-open point, closed


class Scores(NamedTuple):
    """Figures of merit, one entry per design."""

    u_min: FloatVector  # lowest node voltage [p.u.]
    loading: FloatVector  # highest loading of any line or transformer [-]
    loss: FloatVector  # line losses [W]
    objective: FloatVector  # measure cost + loss cost + penalty [k EUR]; inf for an infeasible design


def load_base() -> Dataset:
    """Load the six-bus open ring as power-grid-model input data, with all loads scaled by LOAD_SCALE.

    Returns:
        Row-based input data of the base grid.
    """
    net = pn.simple_mv_open_ring_net()
    data, _extra_info = PandaPowerConverter().load_input_data(net)
    loads = data["sym_load"]
    for power in ("p_specified", "q_specified"):
        loads[power] *= LOAD_SCALE
    return data


def _disabled_copy(
    elements: ComponentArray,
    row: int,
    new_id: int,
    scale: Mapping[str, float] | None = None,
    **values: int,
) -> ComponentArray:
    """Copy one element as a candidate that is switched off at both ends.

    Args:
        elements: Input array that holds the original element (lines or transformers).
        row: Row of the original element in ``elements``.
        new_id: Unused id for the copy.
        scale: Attributes to multiply, as {attribute: factor}.
        **values: Attributes to overwrite, such as ``from_node``.

    Returns:
        A one-row array with the dtype of ``elements``.
    """
    candidate = elements[[row]]  # indexing with a list returns a new one-row array
    candidate["id"] = new_id
    for end in BOTH_ENDS:
        candidate[end] = OFF
    for attribute, factor in (scale or {}).items():
        candidate[attribute] *= factor
    for attribute, value in values.items():
        candidate[attribute] = value
    return candidate


def _id(elements: ComponentArray, row: int = 0) -> int:
    """Return the id of one element as a Python int."""
    return int(elements["id"][row])


def inflate(base: Dataset) -> tuple[Dataset, ElementIds]:
    """Add every candidate measure to the base grid as a disabled element.

    Args:
        base: Input data of the base grid, as returned by load_base(). It is not changed.

    Returns:
        The inflated input data and the ids of the elements that the measures switch.
    """
    data = {component: elements.copy() for component, elements in base.items()}
    lines, trafos = data["line"], data["transformer"]
    largest_id = max(int(elements["id"].max()) for elements in data.values() if len(elements))
    new_ids = itertools.count(largest_id + 1)  # ids are unique across all components

    parallel_12 = _disabled_copy(lines, LINE_12, next(new_ids))
    parallel_61 = _disabled_copy(lines, LINE_61, next(new_ids))
    recond_23 = _disabled_copy(lines, LINE_23, next(new_ids), scale=RECONDUCTOR_FACTORS)
    new_line_25 = _disabled_copy(lines, LINE_12, next(new_ids), scale=NEW_LINE_FACTORS, from_node=2, to_node=5)
    trafo_swap = _disabled_copy(trafos, TRAFO, next(new_ids), scale=TRAFO_SWAP_FACTORS)
    trafo_parallel = _disabled_copy(trafos, TRAFO, next(new_ids))

    data["line"] = np.concatenate([lines, parallel_12, parallel_61, recond_23, new_line_25])
    data["transformer"] = np.concatenate([trafos, trafo_swap, trafo_parallel])
    ids = ElementIds(
        parallel_12=_id(parallel_12),
        parallel_61=_id(parallel_61),
        line_23=_id(lines, LINE_23),
        recond_23=_id(recond_23),
        new_line_25=_id(new_line_25),
        line_56=_id(lines, LINE_56),
        trafo=_id(trafos, TRAFO),
        trafo_swap=_id(trafo_swap),
        trafo_parallel=_id(trafo_parallel),
        line_34=_id(lines, LINE_34),
        line_45=_id(lines, LINE_45),
    )
    return data, ids


def _input_status(component: str, elements: ComponentArray, n_scenarios: int) -> ComponentArray:
    """Return a dense batch update in which every scenario repeats the input status of ``elements``."""
    update = initialize_array("update", component, (n_scenarios, len(elements)))
    update["id"] = elements["id"]
    for end in BOTH_ENDS:
        update[end] = elements[end]
    return update


def status_updates(model_data: Dataset, ids: ElementIds, solutions: npt.ArrayLike) -> Dataset:
    """Turn solution vectors into one dense batch update that only changes element status.

    Each scenario starts from the input status of every line and transformer and then applies
    every measure whose bit is 1.

    Args:
        model_data: Inflated input data, as returned by inflate().
        ids: Ids of the switched elements, as returned by inflate().
        solutions: 0/1 array of shape (n_scenarios, len(MEASURES)), one row per design.

    Returns:
        Batch update with a ``"line"`` and a ``"transformer"`` array of shape (n_scenarios, n_elements).
    """
    chosen = np.asarray(solutions, dtype=bool)  # chosen[s, bit] is True if scenario s applies measure bit
    updates = {
        component: _input_status(component, model_data[component], len(chosen))
        for component in ("line", "transformer")
    }

    def switch(bit: int, component: str, element_id: int, status: int, ends: tuple[str, ...] = BOTH_ENDS) -> None:
        """Set the status of one element in every scenario that applies measure ``bit``."""
        column = int(np.flatnonzero(model_data[component]["id"] == element_id)[0])
        for end in ends:
            updates[component][end][chosen[:, bit], column] = status

    switch(0, "line", ids.parallel_12, ON)  # Parallel line 1-2
    switch(1, "line", ids.parallel_61, ON)  # Parallel line 6-1
    switch(2, "line", ids.line_23, OFF)  # Reconductoring 2-3: original off,
    switch(2, "line", ids.recond_23, ON)  # alternative on
    switch(3, "line", ids.new_line_25, ON)  # New line 2-5
    switch(4, "line", ids.line_56, OFF, ends=FROM_END)  # Open point 5-6: open at bus 5 only
    switch(5, "transformer", ids.trafo, OFF)  # Transformer swap: original off,
    switch(5, "transformer", ids.trafo_swap, ON)  # alternative on
    switch(6, "transformer", ids.trafo_parallel, ON)  # Transformer parallel
    switch(7, "line", ids.line_34, OFF)  # Move open point: open line 3-4,
    switch(7, "line", ids.line_45, ON)  # close line 4-5
    return updates


def feasible(solutions: npt.ArrayLike) -> BoolVector:
    """Mark the designs that keep the grid radial and connected.

    The new line 2-5 and its open point 5-6 must be chosen together: the new line alone closes
    the cycle 2-1-6-5, the open point alone cuts bus 5 off.

    Args:
        solutions: 0/1 array of shape (n_designs, len(MEASURES)).

    Returns:
        True for every feasible design.
    """
    chosen = np.asarray(solutions, dtype=bool)
    new_line, open_point = BOUND
    return chosen[:, new_line] == chosen[:, open_point]


def score(solutions: npt.ArrayLike, result: Dataset) -> Scores:
    """Score every design of a batch power flow.

    Args:
        solutions: 0/1 array of shape (n_designs, len(MEASURES)).
        result: Batch power-flow result with one scenario per design.

    Returns:
        Minimum voltage, maximum loading, line losses and the penalised objective of every design.
    """
    u_min = result["node"]["u_pu"].min(axis=1)
    loading = np.maximum(
        np.nanmax(result["line"]["loading"], axis=1),
        np.nanmax(result["transformer"]["loading"], axis=1),
    )
    loss = result["line"]["p_from"].sum(axis=1) + result["line"]["p_to"].sum(axis=1)
    cost = np.asarray(solutions) @ COST
    voltage_violation = np.maximum(0, U_MIN_LIMIT - u_min)
    loading_violation = np.maximum(0, loading - LOADING_LIMIT)
    penalty = VIOLATION_PENALTY * voltage_violation + VIOLATION_PENALTY * loading_violation
    objective = cost + loss / W_PER_KW * LOSS_COST + penalty * PENALTY_WEIGHT
    return Scores(u_min, loading, loss, np.where(feasible(solutions), objective, np.inf))


def run() -> tuple[Dataset, ElementIds, npt.NDArray[np.int_], Dataset]:
    """Evaluate all 2^8 designs with one inflated model and one batch power flow.

    Returns:
        The inflated input data, the ids of the switched elements, the (256, 8) 0/1 solution matrix
        (row 0 is the all-off vector, the base grid) and the batch result (one scenario per row).
    """
    data, ids = inflate(load_base())
    assert_valid_input_data(data, calculation_type=CalculationType.power_flow)
    model = PowerGridModel(data)
    solutions = np.array(list(itertools.product((0, 1), repeat=len(MEASURES))))
    result = model.calculate_power_flow(
        update_data=status_updates(data, ids, solutions),
        calculation_method=CalculationMethod.newton_raphson,
        continue_on_batch_error=True,  # a design whose power flow fails must not stop the batch
    )
    return data, ids, solutions, result


def main() -> None:
    """Evaluate every design and print the best ones."""
    data, _ids, solutions, result = run()
    scores = score(solutions, result)
    print(f"{len(solutions)} solution vectors, 1 inflated model, 1 batch call")
    print("components:", {component: len(elements) for component, elements in data.items() if len(elements)})
    print("bits:", MEASURES)
    for i in np.argsort(scores.objective)[:N_BEST]:
        print(
            solutions[i],
            f"u_min={scores.u_min[i]:.4f} loading={scores.loading[i]:.2f} "
            f"loss_kW={scores.loss[i] / W_PER_KW:.1f} obj={scores.objective[i]:.1f}",
        )


if __name__ == "__main__":
    main()
