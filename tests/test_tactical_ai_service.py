from __future__ import annotations

import time

import pytest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from main_computer.tactical_ai_service import TacticalAIError, TacticalAIService


class _WarmupDriver:
    latencies = [1.4, 0.8, 0.7]

    def __init__(self, evaluate_url, health, **kwargs):
        self.launch_count = 0
        self._index = 0

    def request_for(self, smoke, ship, trigger):
        return ({"schema": "request"}, {"questionCount": 10})

    def _provider_call(self, payload, meta):
        latency = self.latencies[min(self._index, len(self.latencies) - 1)]
        self._index += 1
        return {
            "wallLatencyMs": latency * 1000.0,
            "response": {
                "schema": "game.captainDecisionResponse.v6",
                "modelLatencyMs": latency * 900.0,
                "answers": [{"choice": "alpha-c0"}] * 10,
            },
        }

    def close(self):
        return None


class _WarmupSmoke:
    def __init__(self, seed, duration, **kwargs):
        self.ships = {"alpha": object(), "beta": object()}


class _BattleDriver:
    def __init__(self, evaluate_url, health, **kwargs):
        self.thoughts = {}
        self.launch_count = 0
        self.completion_count = 0

    def launch_thought(self, smoke, ship, trigger):
        future = Future()
        future.set_result({"action": {"maneuver": "intercept", "weapon": "hold", "defense": "none", "warp": "none"}})
        self.thoughts[ship.id] = future
        self.launch_count += 1
        return True

    def harvest_completed(self, smoke, ship):
        future = self.thoughts.pop(ship.id, None)
        if future is None:
            return None
        self.completion_count += 1
        return dict(future.result()["action"])

    def summary(self):
        return {"enabled": True, "thoughtLaunchCount": self.launch_count, "thoughtCompletionCount": self.completion_count}

    def close(self):
        return None


class _Ship:
    def __init__(self, ship_id):
        self.id = ship_id


class _BattleSmoke:
    def __init__(self, seed, duration, *, live_action_driver=None, live_action_interval_seconds=2.0):
        self.seed = seed
        self.duration = duration
        self.time = 0.0
        self.live_action_driver = live_action_driver
        self.live_action_interval_seconds = live_action_interval_seconds
        self.ships = {"alpha": _Ship("alpha"), "beta": _Ship("beta")}
        self.live_current_actions = {}
        self.live_next_thought_at = {"alpha": 0.0, "beta": 0.0}
        self.events = []
        self.terminal_reason = None

    def ship_snapshot(self, ship):
        return {"id": ship.id, "hull": 1.0, "positionM": [0, 0], "speedMps": 0, "subsystems": {}}

    def run(self):
        assert set(self.live_current_actions) == {"alpha", "beta"}
        self.time = 1.0
        self.terminal_reason = "duration-expired"
        return {
            "ok": True,
            "terminalReason": self.terminal_reason,
            "finishedAtSeconds": 1.0,
            "checks": {"primed": True},
            "metrics": {},
        }


def _wait(thread, timeout=2.0):
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    thread.join(timeout=0.1)
    assert not thread.is_alive()


def test_prepare_requires_consecutive_full_call_latency_passes(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._runtime = SimpleNamespace(LiveActionDriver=_WarmupDriver, CombatSmoke=_WarmupSmoke)
    service._ensure_adapter = lambda startup_timeout: {
        "ok": True,
        "checkpointId": "test",
        "checkpointSha256": "sha",
    }
    service._adapter_evaluate_url = "http://example.invalid/captain/evaluate"

    service.prepare({"time_step_seconds": 1, "consecutive_passes": 2, "warmup_timeout_seconds": 10})
    _wait(service._prepare_thread)
    status = service.status(include_manager=False)

    assert status["phase"] == "performance-ready"
    assert status["performance"]["ready"] is True
    assert status["performance"]["timeStepSeconds"] == 1
    assert status["performance"]["maxLatencySeconds"] == 1.0
    assert status["performance"]["consecutivePasses"] == 2
    assert [sample["passed"] for sample in status["performance"]["samples"]] == [False, True, True]


def test_battle_primes_both_captains_before_simulation_release(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._runtime = SimpleNamespace(LiveActionDriver=_BattleDriver, CombatSmoke=_BattleSmoke)
    service._adapter_health_url = "http://adapter.invalid/health"
    service._adapter_evaluate_url = "http://adapter.invalid/evaluate"
    service._performance_ready = True
    service._tactical_time_step_seconds = 2
    service._warmup_target_seconds = 2.0
    service._phase = "performance-ready"
    service._manager_status = lambda: {"ok": True, "running": True, "backend_ready": True}

    import main_computer.tactical_ai_service as module
    original_get = module._json_get
    module._json_get = lambda url, timeout=2.0: {"ok": True, "checkpointId": "test", "checkpointSha256": "sha"}
    try:
        service.start_battle({"seed": 7, "duration_seconds": 10, "time_step_seconds": 2})
        _wait(service._battle_thread)
    finally:
        module._json_get = original_get

    status = service.status(include_manager=False)
    assert status["phase"] == "complete"
    assert status["battleConfig"]["tacticalTimeStepSeconds"] == 2
    assert status["battleConfig"]["thoughtIntervalSeconds"] == 2.0
    assert status["battleConfig"]["primedCaptainIds"] == ["alpha", "beta"]
    assert status["battleConfig"]["simulationReleasedAfterPrime"] is True
    assert status["battle"]["result"]["ok"] is True


def test_battle_rejects_time_step_that_was_not_performance_gated(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._performance_ready = True
    service._tactical_time_step_seconds = 2
    service._warmup_target_seconds = 2.0
    service._phase = "performance-ready"
    service._manager_status = lambda: {"ok": True, "running": True, "backend_ready": True}

    with pytest.raises(TacticalAIError, match="run Prepare Tactical AI again"):
        service.start_battle({"seed": 7, "duration_seconds": 10, "time_step_seconds": 3})


class _SlowWarmupDriver(_WarmupDriver):
    def _provider_call(self, payload, meta):
        time.sleep(0.15)
        return {
            "wallLatencyMs": 2000.0,
            "response": {
                "schema": "game.captainDecisionResponse.v6",
                "modelLatencyMs": 1800.0,
                "answers": [{"choice": "alpha-c0"}] * 10,
            },
        }


def test_changing_time_step_restarts_inflight_warmup_with_new_gate(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._runtime = SimpleNamespace(LiveActionDriver=_SlowWarmupDriver, CombatSmoke=_WarmupSmoke)
    service._ensure_adapter = lambda startup_timeout: {
        "ok": True,
        "checkpointId": "test",
        "checkpointSha256": "sha",
    }
    service._adapter_evaluate_url = "http://example.invalid/captain/evaluate"

    service.prepare({"time_step_seconds": 1, "consecutive_passes": 3, "warmup_timeout_seconds": 10})
    time.sleep(0.03)
    service.prepare({"time_step_seconds": 5, "consecutive_passes": 1, "warmup_timeout_seconds": 10})
    _wait(service._prepare_thread)

    status = service.status(include_manager=False)
    assert status["phase"] == "performance-ready"
    assert status["performance"]["timeStepSeconds"] == 5
    assert status["performance"]["maxLatencySeconds"] == 5.0
    assert status["performance"]["consecutivePasses"] == 1
    assert status["performance"]["lastSample"]["wallLatencySeconds"] == 2.0
    assert status["performance"]["lastSample"]["passed"] is True


def test_reset_cancels_inflight_warmup_thread(tmp_path: Path):
    service = TacticalAIService(tmp_path)
    service._runtime = SimpleNamespace(LiveActionDriver=_SlowWarmupDriver, CombatSmoke=_WarmupSmoke)
    service._ensure_adapter = lambda startup_timeout: {
        "ok": True,
        "checkpointId": "test",
        "checkpointSha256": "sha",
    }
    service._adapter_evaluate_url = "http://example.invalid/captain/evaluate"

    service.prepare({"time_step_seconds": 1, "consecutive_passes": 3, "warmup_timeout_seconds": 10})
    prepare_thread = service._prepare_thread
    time.sleep(0.03)
    status = service.reset()

    assert prepare_thread is not None
    assert not prepare_thread.is_alive()
    assert status["phase"] == "idle"
    assert status["performance"]["ready"] is False
    assert status["performance"]["samples"] == []
