from __future__ import annotations

from copy import deepcopy

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-005"],
    operations=["MOTHER-OP-ADD-NODE"],
    functionalities=["MOTHER-OF-AUTH-004"],
    modules=["MOTHER-OFM-CORE-013"],
)


def _faultpoints():
    return require_mother_module("common.faultpoints", "MOTHER-OFM-CORE-013")


def test_module_level_production_hook_is_a_true_noop() -> None:
    faultpoints = _faultpoints()
    context = {"operation_id": "operation-1", "nested": {"value": 1}}
    before = deepcopy(context)
    assert faultpoints.hit("journal.before_head_pair_commit", context) is None
    assert context == before


def test_production_controller_is_a_true_noop() -> None:
    faultpoints = _faultpoints()
    controller = faultpoints.FaultpointController.production()
    context = {"attempt": 1}
    assert controller.hit("immutable.after_temp_write", context) is None
    assert context == {"attempt": 1}


def test_injected_controller_interrupts_on_the_exact_scheduled_hit() -> None:
    faultpoints = _faultpoints()
    controller = faultpoints.FaultpointController.injected(
        {"journal.before_head_pair_commit": (2,)}
    )
    controller.hit("journal.before_head_pair_commit", {"attempt": 1})
    with pytest.raises(faultpoints.SimulatedInterruption) as raised:
        controller.hit("journal.before_head_pair_commit", {"attempt": 2})
    assert raised.value.faultpoint == "journal.before_head_pair_commit"
    assert raised.value.hit_number == 2
    controller.hit("journal.before_head_pair_commit", {"attempt": 3})


def test_injected_schedule_is_deterministic_across_instances() -> None:
    faultpoints = _faultpoints()
    outcomes = []
    for _ in range(2):
        controller = faultpoints.FaultpointController.injected(
            {"pointer.before_cas": (1, 3)}
        )
        run = []
        for number in range(1, 5):
            try:
                controller.hit("pointer.before_cas", {"number": number})
            except faultpoints.SimulatedInterruption:
                run.append(number)
        outcomes.append(run)
    assert outcomes == [[1, 3], [1, 3]]


def test_faultpoint_never_mutates_context_or_suppresses_other_errors() -> None:
    faultpoints = _faultpoints()
    controller = faultpoints.FaultpointController.injected(
        {"rollback.after_frame_arm": (1,)}
    )
    context = {"secret": "opaque", "values": [1, 2]}
    before = deepcopy(context)
    with pytest.raises(faultpoints.SimulatedInterruption):
        controller.hit("rollback.after_frame_arm", context)
    assert context == before

    with pytest.raises(RuntimeError, match="original"):
        try:
            raise RuntimeError("original")
        except RuntimeError:
            controller = faultpoints.FaultpointController.production()
            controller.hit("unrelated", {})
            raise

@pytest.mark.parametrize(
    "schedule",
    [
        {"pointer.before_cas": True},
        {"pointer.before_cas": (True,)},
        {"pointer.before_cas": (1, False)},
    ],
)
def test_boolean_hit_counts_are_rejected(schedule) -> None:
    faultpoints = _faultpoints()
    with pytest.raises((TypeError, ValueError)):
        faultpoints.FaultpointController.injected(schedule)


def test_simulated_interruption_rejects_boolean_hit_number() -> None:
    faultpoints = _faultpoints()
    with pytest.raises((TypeError, ValueError)):
        faultpoints.SimulatedInterruption("pointer.before_cas", True)

