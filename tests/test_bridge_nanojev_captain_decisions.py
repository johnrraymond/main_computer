"""Bridge decisions use the actual NanoJev adapter protocol, without launching a second battle."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.tactical_ai_service import TacticalAIError, TacticalAIService


def observation(**overrides):
    item = {
        "schema": "game.bridgeCaptainObservation.v1",
        "captainId": "captain.beta", "shipId": "ship.beta",
        "simulationSeconds": 3.0, "rangeM": 2500.0,
        "radialVelocityMps": -85.0, "targetHullPercent": 100.0,
        "transporterMaxRangeM": 2500.0,
        "relativePositionM": [2500.0, 0.0], "relativeVelocityMps": [-85.0, 0.0],
        "mission": "pursue-main-ship-and-seek-boarding-range",
    }
    item.update(overrides)
    return item


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload


def ready_service(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._performance_ready = True
    service._model_loaded = True
    service._adapter_evaluate_url = "http://adapter.test/captain/evaluate"
    service._adapter_health = {"checkpointId": "real-checkpoint", "checkpointSha256": "release-hash"}
    service._adapter_alive = lambda: True
    service._manager_status = lambda: {
        "ok": True, "running": True, "backend_ready": True,
        "model_loaded": True, "checkpoint_validated": True,
    }
    return service


def test_real_model_adapter_receipt_and_choice_are_validated(monkeypatch, tmp_path):
    svc = ready_service(tmp_path)
    outgoing = []

    def call(request, timeout):
        assert request.full_url.endswith("/captain/evaluate")
        payload = json.loads(request.data.decode())
        outgoing.append(payload)
        assert payload["schema"] == "game.captainDecisionRequest.v6"
        assert "2500.00m" in payload["semanticContext"]["text"]
        assert payload["battle"]["observation"]["positionM"] == [2500.0, 0.0]
        assert payload["battle"]["observation"]["velocityMps"] == [-85.0, 0.0]
        assert len(payload["questions"]) == 10
        assert len(payload["tacticalControls"]) == 5
        answers = []
        for q in payload["questions"]:
            choice = "beta-c3" if "beta-c3" in [q["optionA"], q["optionB"]] else q["optionA"]
            answers.append({"choice": choice, "margin": 0.1})
        return FakeResponse({"schema": "game.captainDecisionResponse.v6", "modelLatencyMs": 15,
                             "answers": answers})

    monkeypatch.setattr("main_computer.tactical_ai_service.urllib.request.urlopen", call)
    decision = svc.decide_bridge_captain(observation())
    assert decision["ok"] is True
    assert decision["source"] == "nanojev-captain-v6"
    assert decision["maneuver"] == "withdraw"
    assert decision["rangeM"] is None
    assert decision["modelReceipt"]["checkpointSha256"] == "release-hash"
    assert decision["modelReceipt"]["chosenCandidateId"] == "beta-c3"
    assert len(outgoing) == 1


def test_model_must_be_ready_before_bridge_inference(tmp_path):
    svc = TacticalAIService(tmp_path)
    with pytest.raises(TacticalAIError, match="NOT_READY"):
        svc.decide_bridge_captain(observation())


@pytest.mark.parametrize("changes,reason", [
    ({"captainId": "captain.alpha"}, "WRONG_AUTHORITY"),
    ({"rangeM": float("nan")}, "INVALID_rangeM"),
    ({"relativePositionM": [1, 2]}, "RELATIVE_RANGE_MISMATCH"),
    ({"relativeVelocityMps": [float("inf"), 0]}, "INVALID_relativeVelocityMps"),
    ({"mission": "something-else"}, "MISSION_REQUIRED"),
])
def test_bad_live_observation_never_invokes_model(monkeypatch, tmp_path, changes, reason):
    svc = ready_service(tmp_path)
    monkeypatch.setattr("main_computer.tactical_ai_service.urllib.request.urlopen",
                        lambda *a, **kw: pytest.fail("model must not be invoked"))
    with pytest.raises(TacticalAIError, match=reason):
        svc.decide_bridge_captain(observation(**changes))


def test_malformed_model_answer_does_not_become_captain_order(monkeypatch, tmp_path):
    svc = ready_service(tmp_path)
    def bad_call(request, timeout):
        return FakeResponse({"schema": "game.captainDecisionResponse.v6", "answers": [
            {"choice": "not-an-authorized-action", "margin": 0.0} for _ in range(10)
        ]})
    monkeypatch.setattr("main_computer.tactical_ai_service.urllib.request.urlopen", bad_call)
    with pytest.raises(TacticalAIError, match="ANSWER_NOT_IN_PAIR"):
        svc.decide_bridge_captain(observation())
