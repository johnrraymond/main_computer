from main_computer.mcel_slh_application import observe_slh


def test_valid_service_does_not_claim_worker_inventory():
    payload = {"schema": "main-computer.slh-local-status.v1",
               "local_service": {"state": "running", "binding": "loopback"},
               "initialization": {"state": "ready"}, "organization_service": {"state": "running"}}
    result = observe_slh("overview", probe=lambda: ("RUNNING", payload))
    assert result["overview"]["organizationState"] == "running"
    assert result["workerSummary"]["observationState"] == "AUTH_REQUIRED"
    assert result["workers"] == []
