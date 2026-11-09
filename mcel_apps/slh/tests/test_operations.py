from main_computer.mcel_slh_application import observe_slh


def test_unreachable_service_is_not_an_empty_worker_fleet():
    result = observe_slh("workers", probe=lambda: ("UNREACHABLE", {}))
    assert result["workers"] == []
    assert result["workerSummary"]["observationState"] == "NOT_OBSERVED"
    assert "does not mean zero" in result["workerSummary"]["explanation"]
