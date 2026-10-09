from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any


class TacticalAIError(RuntimeError):
    pass


def _json_get(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise TacticalAIError(f"Expected JSON object from {url}")
    return data


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TacticalAIService:
    """Game-facing controller for the proven Space Captain/NanoJev combat runtime.

    The viewport owns this controller, but NanoJev lifecycle ownership remains with
    nanojev_lifecycle_service.py. Status polling never hits the captain adapter's
    /health endpoint, because doing so would count as backend traffic and defeat the
    manager's idle-unload behavior.
    """

    schema = "main-computer.tactical-ai-service.v1"

    def __init__(self, root: Path, *, manager_url: str = "http://127.0.0.1:9765") -> None:
        self.root = Path(root).resolve()
        self.manager_url = str(manager_url).rstrip("/")
        self._lock = threading.RLock()
        self._runtime: ModuleType | None = None
        self._adapter_process: subprocess.Popen[str] | None = None
        self._adapter_log_path: Path | None = None
        self._adapter_health_url = ""
        self._adapter_evaluate_url = ""
        self._adapter_health: dict[str, Any] = {}
        self._prepare_thread: threading.Thread | None = None
        self._prepare_cancel_event = threading.Event()
        self._battle_thread: threading.Thread | None = None
        self._smoke: Any = None
        self._driver: Any = None
        self._battle_result: dict[str, Any] | None = None
        self._phase = "idle"
        self._last_error = ""
        self._performance_ready = False
        self._model_loaded = False
        self._warmup_samples: list[dict[str, Any]] = []
        self._warmup_target_seconds = 5.0
        self._tactical_time_step_seconds = 5
        self._warmup_required_consecutive = 3
        self._warmup_consecutive_passes = 0
        self._warmup_timeout_seconds = 120.0
        # Watchdog starts only after the captain adapter becomes healthy. A slow
        # cold model load is not a failed tactical inference.
        self._no_good_result_reset_seconds = 20.0
        self._warmup_soft_reset_count = 0
        self._warmup_last_soft_reset_reason = ""
        self._prepare_started_monotonic: float | None = None
        self._battle_started_monotonic: float | None = None
        self._battle_config: dict[str, Any] = {}

    def _runtime_module(self) -> ModuleType:
        with self._lock:
            if self._runtime is not None:
                return self._runtime
        path = self.root / "game_projects" / "webgl-demo" / "tools" / "space_captain_fleshed_combat_smoke.py"
        if not path.is_file():
            raise TacticalAIError(f"Space Captain combat runtime not found: {path}")
        name = "main_computer_space_captain_fleshed_combat_runtime"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise TacticalAIError(f"Could not load Space Captain runtime: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        with self._lock:
            self._runtime = module
        return module

    def _manager_status(self) -> dict[str, Any]:
        try:
            data = _json_get(self.manager_url + "/control/status", timeout=1.5)
            return data if data.get("ok") is True else {"ok": False, "error": data.get("error", "manager status failed")}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _manager_model_ready(manager: dict[str, Any]) -> bool:
        return (
            manager.get("ok") is True
            and manager.get("running") is True
            and manager.get("backend_ready") is True
            and manager.get("model_loaded") is True
            and manager.get("checkpoint_validated") is True
        )

    def _adapter_alive(self) -> bool:
        process = self._adapter_process
        return bool(process is not None and process.poll() is None)

    def _terminate_adapter(self) -> None:
        process = self._adapter_process
        self._adapter_process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        self._adapter_health_url = ""
        self._adapter_evaluate_url = ""
        self._adapter_health = {}

    def _ensure_adapter(self, startup_timeout_seconds: float) -> dict[str, Any]:
        if self._adapter_alive() and self._adapter_health_url:
            try:
                health = _json_get(self._adapter_health_url, timeout=max(5.0, min(30.0, startup_timeout_seconds)))
                if health.get("ok") is True:
                    self._adapter_health = dict(health)
                    return health
            except Exception:
                self._terminate_adapter()

        runtime = self._runtime_module()
        nanojev_python = runtime._default_nanojev_python().resolve(strict=True)
        live_backend = Path(runtime.LIVE_BACKEND).resolve(strict=True)
        runtime_dir = self.root / "runtime" / "captain_live_clef"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        log_path = runtime_dir / f"tactical-ai-{int(time.time())}-{port}.log"
        command = [
            str(nanojev_python),
            str(live_backend),
            "--service-url",
            self.manager_url,
            "--port",
            str(port),
        ]
        handle = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        finally:
            handle.close()
        self._adapter_process = process
        self._adapter_log_path = log_path
        self._adapter_health_url = f"http://127.0.0.1:{port}/health"
        self._adapter_evaluate_url = f"http://127.0.0.1:{port}/captain/evaluate"

        started = time.monotonic()
        last_error = ""
        while time.monotonic() - started < startup_timeout_seconds:
            code = process.poll()
            if code is not None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:] if log_path.is_file() else ""
                raise TacticalAIError(f"Tactical captain adapter exited during startup with code {code}: {tail}")
            try:
                health = _json_get(self._adapter_health_url, timeout=2.0)
                if health.get("ok") is True:
                    self._adapter_health = dict(health)
                    return health
                last_error = str(health.get("error") or health)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        raise TacticalAIError(
            f"Tactical captain adapter did not become healthy within {startup_timeout_seconds:.1f}s; "
            f"last_error={last_error}; log={log_path}"
        )

    def _ensure_model_loaded(self, startup_timeout_seconds: float, cancel_event: threading.Event) -> None:
        """Wake the lazy manager and verify a loaded checkpoint before timed inference.

        The manager's /api/health proxy starts the container if necessary. Unlike
        the lightweight captain adapter /health, this endpoint explicitly reports
        model_loaded_once and is validated by the manager's checkpoint-aware health.
        Cold startup is outside the 20-second inference watchdog.
        """
        if cancel_event.is_set():
            return
        try:
            backend_health = _json_get(
                self.manager_url + "/api/health",
                timeout=max(5.0, startup_timeout_seconds),
            )
        except Exception as exc:
            raise TacticalAIError(f"NanoJev model load did not complete: {type(exc).__name__}: {exc}") from exc
        if cancel_event.is_set():
            return
        if backend_health.get("ready") is not True or backend_health.get("model_loaded_once") is not True:
            raise TacticalAIError("NanoJev did not confirm ready=true and model_loaded_once=true")
        manager = self._manager_status()
        if not self._manager_model_ready(manager):
            raise TacticalAIError("NanoJev's model responded, but the manager has not confirmed checkpoint readiness")
        with self._lock:
            self._model_loaded = True

    @staticmethod
    def _positive_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _positive_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def prepare(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        raw_time_step = config.get("time_step_seconds")
        if raw_time_step is None:
            # Backward-compatible fallback for callers from the first Tactical AI harness.
            raw_time_step = config.get("max_latency_seconds")
        time_step = self._positive_int(raw_time_step, 5, 1, 5)
        target = float(time_step)
        required = self._positive_int(config.get("consecutive_passes"), 3, 1, 20)
        timeout = self._positive_float(config.get("warmup_timeout_seconds"), 120.0, 5.0, 900.0)
        startup_timeout = self._positive_float(config.get("startup_timeout_seconds"), 180.0, 5.0, 900.0)
        request_timeout = self._positive_float(config.get("request_timeout_seconds"), 300.0, 5.0, 900.0)

        previous_thread = None
        previous_cancel = None
        with self._lock:
            if self._battle_thread is not None and self._battle_thread.is_alive():
                raise TacticalAIError("Cannot prepare Tactical AI while a battle is running.")
            if self._prepare_thread is not None and self._prepare_thread.is_alive():
                same_config = (
                    int(self._tactical_time_step_seconds) == time_step
                    and int(self._warmup_required_consecutive) == required
                    and abs(float(self._warmup_timeout_seconds) - timeout) < 1e-9
                )
                if same_config:
                    return self.status(include_manager=False)
                previous_thread = self._prepare_thread
                previous_cancel = self._prepare_cancel_event
                previous_cancel.set()
                self._phase = "restarting-ai"

        if previous_thread is not None and previous_thread.is_alive():
            previous_thread.join(timeout=min(10.0, max(2.0, request_timeout)))
            if previous_thread.is_alive():
                raise TacticalAIError(
                    "Previous Tactical AI warmup is still finishing an in-flight call; retry the time-step change shortly."
                )

        cancel_event = threading.Event()
        with self._lock:
            self._phase = "starting-ai"
            self._last_error = ""
            self._performance_ready = False
            self._model_loaded = False
            self._warmup_samples = []
            self._warmup_target_seconds = target
            self._tactical_time_step_seconds = time_step
            self._warmup_required_consecutive = required
            self._warmup_consecutive_passes = 0
            self._warmup_timeout_seconds = timeout
            self._warmup_soft_reset_count = 0
            self._warmup_last_soft_reset_reason = ""
            self._prepare_started_monotonic = time.monotonic()
            self._prepare_cancel_event = cancel_event
            thread = threading.Thread(
                target=self._prepare_worker,
                args=(startup_timeout, request_timeout, cancel_event),
                name="tactical-ai-prepare",
                daemon=True,
            )
            self._prepare_thread = thread
            thread.start()
        return self.status(include_manager=False)

    def _prepare_worker(
        self,
        startup_timeout_seconds: float,
        request_timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> None:
        driver = None
        try:
            with self._lock:
                self._phase = "loading-model"
            self._ensure_model_loaded(startup_timeout_seconds, cancel_event)
            if cancel_event.is_set():
                return
            health = self._ensure_adapter(startup_timeout_seconds)
            if cancel_event.is_set():
                return
            runtime = self._runtime_module()
            driver = runtime.LiveActionDriver(
                self._adapter_evaluate_url,
                health,
                request_timeout_seconds=min(request_timeout_seconds, self._no_good_result_reset_seconds),
                include_call_snapshots=False,
            )
            smoke = runtime.CombatSmoke(7, 120.0)
            ship = smoke.ships["alpha"]
            with self._lock:
                self._phase = "warming-ai"
            started = time.monotonic()
            last_passing_result_at = started
            last_failure = ""
            sample_index = 0
            while not cancel_event.is_set() and time.monotonic() - started < self._warmup_timeout_seconds:
                sample_index += 1
                driver.launch_count = sample_index - 1
                payload, meta = driver.request_for(smoke, ship, "performance-warmup")
                try:
                    result = driver._provider_call(payload, meta)
                except Exception as exc:
                    if cancel_event.is_set():
                        return
                    last_failure = f"{type(exc).__name__}: {exc}"
                    result = None
                if cancel_event.is_set():
                    return
                # An eventually returned answer does not retroactively satisfy a
                # watchdog that expired while the provider call was in flight.
                watchdog_expired = time.monotonic() - last_passing_result_at >= self._no_good_result_reset_seconds
                if watchdog_expired and result is not None:
                    last_failure = "Inference completed after the no-good-result timeout"
                if result is not None and not watchdog_expired:
                    response = dict(result.get("response") or {})
                    answers = list(response.get("answers") or [])
                    if response.get("schema") != "game.captainDecisionResponse.v6" or len(answers) != int(meta["questionCount"]):
                        last_failure = "Invalid captain response schema or answer count"
                    else:
                        latency_seconds = float(result.get("wallLatencyMs") or 0.0) / 1000.0
                        passed = latency_seconds <= self._warmup_target_seconds
                        with self._lock:
                            self._warmup_consecutive_passes = self._warmup_consecutive_passes + 1 if passed else 0
                            self._warmup_samples.append({
                                "index": sample_index,
                                "wallLatencySeconds": latency_seconds,
                                "modelLatencyMs": float(response.get("modelLatencyMs") or 0.0),
                                "questionCount": int(meta["questionCount"]),
                                "passed": passed,
                            })
                            self._warmup_samples = self._warmup_samples[-24:]
                            if passed:
                                last_passing_result_at = time.monotonic()
                            if self._warmup_consecutive_passes >= self._warmup_required_consecutive:
                                self._performance_ready = True
                                self._phase = "performance-ready"
                                return
                if time.monotonic() - last_passing_result_at >= self._no_good_result_reset_seconds:
                    with self._lock:
                        if self._warmup_soft_reset_count >= 1:
                            raise TacticalAIError(
                                f"No passing Tactical AI inference for {self._no_good_result_reset_seconds:g}s "
                                f"after a soft reset. Last failure: {last_failure or 'calls exceeded the performance gate'}"
                            )
                        self._warmup_soft_reset_count += 1
                        self._warmup_last_soft_reset_reason = (
                            f"No passing Tactical AI inference for {self._no_good_result_reset_seconds:g}s; "
                            f"last failure: {last_failure or 'calls exceeded the performance gate'}"
                        )
                        self._phase = "restarting-ai"
                        self._warmup_consecutive_passes = 0
                        self._warmup_samples = []
                    # Kill only the game-specific adapter process. NanoJev's
                    # Docker container, model checkpoint and training are untouched.
                    driver.close()
                    driver = None
                    with self._lock:
                        if cancel_event.is_set():
                            return
                        self._terminate_adapter()
                    if cancel_event.is_set():
                        return
                    self._ensure_model_loaded(startup_timeout_seconds, cancel_event)
                    if cancel_event.is_set():
                        return
                    health = self._ensure_adapter(startup_timeout_seconds)
                    if cancel_event.is_set():
                        return
                    driver = runtime.LiveActionDriver(
                        self._adapter_evaluate_url,
                        health,
                        request_timeout_seconds=min(request_timeout_seconds, self._no_good_result_reset_seconds),
                        include_call_snapshots=False,
                    )
                    with self._lock:
                        if not cancel_event.is_set():
                            self._phase = "warming-ai"
                    last_passing_result_at = time.monotonic()
                    last_failure = ""
                pause = min(0.25 if result is None else 0.05, self._no_good_result_reset_seconds / 10.0)
                if cancel_event.wait(pause):
                    return
            if cancel_event.is_set():
                return
            raise TacticalAIError(
                f"NanoJev became functionally ready but did not achieve <= {self._warmup_target_seconds:.3f}s "
                f"for {self._warmup_required_consecutive} consecutive representative captain calls within "
                f"{self._warmup_timeout_seconds:.1f}s."
            )
        except Exception as exc:
            if cancel_event.is_set():
                return
            with self._lock:
                self._performance_ready = False
                self._phase = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if driver is not None:
                driver.close()

    def _require_runtime_ready(self) -> None:
        manager = self._manager_status()
        if not self._manager_model_ready(manager):
            with self._lock:
                self._performance_ready = False
                if self._phase == "performance-ready":
                    self._phase = "idle"
            raise TacticalAIError("NanoJev backend is no longer performance-ready; run Prepare Tactical AI again.")

    def start_battle(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        with self._lock:
            if not self._performance_ready:
                raise TacticalAIError("Tactical AI is not performance-ready. Prepare it before starting the battle.")
            if self._prepare_thread is not None and self._prepare_thread.is_alive():
                raise TacticalAIError("Tactical AI is still warming.")
            if self._battle_thread is not None and self._battle_thread.is_alive():
                raise TacticalAIError("A tactical battle is already running.")
        self._require_runtime_ready()

        seed = self._positive_int(config.get("seed"), 7, 0, 2_147_483_647)
        duration = self._positive_float(config.get("duration_seconds"), 120.0, 1.0, 3600.0)
        with self._lock:
            prepared_time_step = int(self._tactical_time_step_seconds)
        raw_time_step = config.get("time_step_seconds")
        if raw_time_step is None:
            # Backward-compatible fallback for callers from the first Tactical AI harness.
            raw_time_step = config.get("thought_interval_seconds", prepared_time_step)
        time_step = self._positive_int(raw_time_step, prepared_time_step, 1, 5)
        if time_step != prepared_time_step:
            raise TacticalAIError(
                f"Tactical time step changed from {prepared_time_step}s to {time_step}s; "
                "run Prepare Tactical AI again so the performance gate matches the battle cadence."
            )
        interval = float(time_step)
        request_timeout = self._positive_float(config.get("request_timeout_seconds"), 300.0, 5.0, 900.0)
        include_call_snapshots = bool(config.get("include_call_snapshots", False))
        with self._lock:
            self._phase = "priming-battle"
            self._last_error = ""
            self._battle_result = None
            self._battle_config = {
                "seed": seed,
                "durationSeconds": duration,
                "tacticalTimeStepSeconds": time_step,
                "thoughtIntervalSeconds": interval,
                "requestTimeoutSeconds": request_timeout,
                "includeCallSnapshots": include_call_snapshots,
            }
            thread = threading.Thread(
                target=self._battle_worker,
                args=(seed, duration, interval, request_timeout, include_call_snapshots),
                name="tactical-ai-battle",
                daemon=True,
            )
            self._battle_thread = thread
            thread.start()
        return self.status(include_manager=False)

    def _battle_worker(
        self,
        seed: int,
        duration: float,
        interval: float,
        request_timeout: float,
        include_call_snapshots: bool,
    ) -> None:
        driver = None
        try:
            # Reuse the already-warmed adapter; this health call is intentional because
            # starting a battle is activity and may wake the backend if it disappeared.
            health = _json_get(self._adapter_health_url, timeout=min(30.0, request_timeout))
            if health.get("ok") is not True:
                raise TacticalAIError(f"Tactical captain adapter is not healthy: {health}")
            self._adapter_health = dict(health)
            runtime = self._runtime_module()
            driver = runtime.LiveActionDriver(
                self._adapter_evaluate_url,
                health,
                request_timeout_seconds=request_timeout,
                include_call_snapshots=include_call_snapshots,
            )
            smoke = runtime.CombatSmoke(
                seed,
                duration,
                live_action_driver=driver,
                live_action_interval_seconds=interval,
            )
            with self._lock:
                self._driver = driver
                self._smoke = smoke

            # Prime both captains before simulation time starts. This removes the old
            # opening fallback window: t=0 is not released until both real NanoJev
            # captain packages have published.
            for ship in smoke.ships.values():
                driver.launch_thought(smoke, ship, "initial")
            prime_deadline = time.monotonic() + request_timeout
            while driver.thoughts and time.monotonic() < prime_deadline:
                for ship in smoke.ships.values():
                    completed = driver.harvest_completed(smoke, ship)
                    if completed is not None:
                        smoke.live_current_actions[ship.id] = completed
                        smoke.live_next_thought_at[ship.id] = smoke.time + interval
                if driver.thoughts:
                    time.sleep(0.01)
            if driver.thoughts:
                raise TacticalAIError("Timed out waiting for initial captain plans before t=0.")
            if set(smoke.live_current_actions) != {"alpha", "beta"}:
                raise TacticalAIError(f"Initial captain plans incomplete: {sorted(smoke.live_current_actions)}")

            with self._lock:
                self._battle_config["primedCaptainIds"] = sorted(smoke.live_current_actions)
                self._battle_config["simulationReleasedAfterPrime"] = True
                self._phase = "running"
                self._battle_started_monotonic = time.monotonic()
            result = smoke.run()
            with self._lock:
                self._battle_result = result
                self._phase = "complete"
        except Exception as exc:
            with self._lock:
                self._phase = "error"
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if driver is not None:
                driver.close()
            with self._lock:
                self._driver = None

    def stop_battle(self) -> dict[str, Any]:
        with self._lock:
            smoke = self._smoke
            thread = self._battle_thread
            if thread is None or not thread.is_alive() or smoke is None:
                return self.status(include_manager=False)
            self._phase = "stopping-battle"
            smoke.duration = min(float(smoke.duration), float(smoke.time) + 0.2)
        return self.status(include_manager=False)

    def reset(self) -> dict[str, Any]:
        self.stop_battle()
        battle_thread = self._battle_thread
        if battle_thread is not None and battle_thread.is_alive():
            battle_thread.join(timeout=5.0)

        with self._lock:
            prepare_thread = self._prepare_thread
            self._prepare_cancel_event.set()
            self._terminate_adapter()
        if prepare_thread is not None and prepare_thread.is_alive():
            prepare_thread.join(timeout=5.0)

        with self._lock:
            self._performance_ready = False
            self._model_loaded = False
            self._warmup_samples = []
            self._warmup_consecutive_passes = 0
            self._warmup_soft_reset_count = 0
            self._warmup_last_soft_reset_reason = ""
            self._phase = "idle"
            self._last_error = ""
            self._smoke = None
            self._driver = None
            self._battle_result = None
            self._battle_thread = None
            self._prepare_thread = None
            self._prepare_cancel_event = threading.Event()
            self._battle_config = {}
        return self.status(include_manager=False)

    def _battle_snapshot(self) -> dict[str, Any] | None:
        smoke = self._smoke
        if smoke is None:
            return None
        try:
            ships = {ship_id: smoke.ship_snapshot(ship) for ship_id, ship in smoke.ships.items()}
            live = smoke.live_action_driver.summary() if smoke.live_action_driver is not None else {"enabled": False}
            return {
                "running": bool(self._battle_thread is not None and self._battle_thread.is_alive()),
                "simulationTimeSeconds": float(smoke.time),
                "durationSeconds": float(smoke.duration),
                "terminalReason": smoke.terminal_reason,
                "ships": ships,
                "currentActions": {key: dict(value) for key, value in smoke.live_current_actions.items()},
                "liveAction": live,
                "recentEvents": [dict(event) for event in smoke.events[-40:]],
                "result": (
                    {
                        "ok": self._battle_result.get("ok"),
                        "terminalReason": self._battle_result.get("terminalReason"),
                        "finishedAtSeconds": self._battle_result.get("finishedAtSeconds"),
                        "checks": dict(self._battle_result.get("checks") or {}),
                        "metrics": dict(self._battle_result.get("metrics") or {}),
                    }
                    if self._battle_result is not None
                    else None
                ),
            }
        except Exception as exc:
            return {"running": False, "snapshotError": f"{type(exc).__name__}: {exc}"}

    def status(self, *, include_manager: bool = True) -> dict[str, Any]:
        manager = self._manager_status() if include_manager else None
        if manager and manager.get("ok") is True:
            if not self._manager_model_ready(manager):
                with self._lock:
                    self._model_loaded = False
                    if self._performance_ready and not (self._battle_thread and self._battle_thread.is_alive()):
                        self._performance_ready = False
                        if self._phase == "performance-ready":
                            self._phase = "idle"
        with self._lock:
            samples = [dict(item) for item in self._warmup_samples]
            last_sample = dict(samples[-1]) if samples else None
            return {
                "ok": not bool(self._last_error),
                "schema": self.schema,
                "phase": self._phase,
                "lastError": self._last_error,
                "managerUrl": self.manager_url,
                "manager": manager,
                "modelLoaded": (
                    self._manager_model_ready(manager)
                    if manager is not None and manager.get("ok") is True
                    else self._model_loaded if manager is None else False
                ),
                "adapter": {
                    "running": self._adapter_alive(),
                    "healthUrl": self._adapter_health_url or None,
                    "checkpointId": self._adapter_health.get("checkpointId"),
                    "checkpointSha256": self._adapter_health.get("checkpointSha256"),
                    "cycle": self._adapter_health.get("cycle"),
                    "provider": self._adapter_health.get("provider"),
                    "logPath": str(self._adapter_log_path) if self._adapter_log_path else None,
                },
                "performance": {
                    "ready": self._performance_ready,
                    "timeStepSeconds": self._tactical_time_step_seconds,
                    "maxLatencySeconds": self._warmup_target_seconds,
                    "requiredConsecutivePasses": self._warmup_required_consecutive,
                    "consecutivePasses": self._warmup_consecutive_passes,
                    "warmupTimeoutSeconds": self._warmup_timeout_seconds,
                    "noGoodResultResetSeconds": self._no_good_result_reset_seconds,
                    "softResetCount": self._warmup_soft_reset_count,
                    "lastSoftResetReason": self._warmup_last_soft_reset_reason,
                    "lastSample": last_sample,
                    "samples": samples,
                },
                "battleConfig": dict(self._battle_config),
                "battle": self._battle_snapshot(),
            }

    def close(self) -> None:
        try:
            self.stop_battle()
            battle_thread = self._battle_thread
            if battle_thread is not None and battle_thread.is_alive():
                battle_thread.join(timeout=5.0)
            with self._lock:
                prepare_thread = self._prepare_thread
                self._prepare_cancel_event.set()
                self._terminate_adapter()
            if prepare_thread is not None and prepare_thread.is_alive():
                prepare_thread.join(timeout=5.0)
        finally:
            with self._lock:
                self._performance_ready = False
                self._phase = "idle"
