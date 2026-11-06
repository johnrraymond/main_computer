"""Real HTTP shared-backend contract (one loaded checkpoint, multiple Workers)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from main_computer.providers.nanojev import (
    CLEF_EVALUATE, CLEF_PAIRWISE, NanoJevClefProvider, NanoJevProviderError,
)
from main_computer.providers.nanojev_worker import NanoJevSharedWorkerBackend
from main_computer.model_execution_contract import ModelContractError

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64


def service_module():
    path = ROOT / "docker" / "nanojev" / "clef_service.py"
    spec = importlib.util.spec_from_file_location("nanojev_patch2_service", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def native_payload(tag="s"):
    return {"states": [{"id": tag, "state": "A tactical problem", "questions": {
        "q": {"type": "choice", "instructions": "Choose", "criteria": {
            "left": "Go left", "right": "Go right"}},
    }}]}


def offer(model="nanojev-clef", operation=CLEF_EVALUATE, tag="s"):
    return {"work": {"model": model, "operation": operation,
                     "capabilities": [operation], "input": {"kind": operation,
                                                   "payload": native_payload(tag)}}}


class CheckpointRuntime:
    """GPU-free runtime preserving the real loaded-runtime dispatch method."""
    def __init__(self, mod):
        self.mod = mod
        self.lock = threading.RLock()
        self.release_manifest_sha256 = SHA
        self.release_name = "CLEF-test"
        self.repo_id = "test/CLEF"
        self.resolved_revision = "test-rev"
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.initializations = 1

    def model_catalog(self):
        return self.mod.ClefRuntime.model_catalog(self)

    def infer(self, req):
        return self.mod.ClefRuntime.infer(self, req)

    def evaluate(self, payload):
        with self.lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            try:
                self.calls += 1
                time.sleep(.012)  # exercise genuine concurrent HTTP clients
                return {"model": {"family": "nanojev-clef", "release_name": self.release_name,
                                  "release_manifest_sha256": self.release_manifest_sha256},
                        "states": [{"id": row["id"], "answers": {"q": {
                            "type": "choice", "choice": "left", "value": "left",
                            "probabilities": {"left": 0.6, "right": 0.4},
                        }}} for row in payload["states"]]}
            finally:
                self.concurrent -= 1

    def evaluate_pairwise_batch(self, payload):
        raise AssertionError("not used by this test")


@pytest.fixture
def real_http_backend():
    mod = service_module()
    runtime = CheckpointRuntime(mod)
    server = mod.ClefServer(("127.0.0.1", 0), runtime)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", runtime, server, mod
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=2)


def test_service_model_catalog_is_actual_loaded_checkpoint(real_http_backend):
    url, runtime, _, _ = real_http_backend
    catalog = NanoJevClefProvider(base_url=url).discover_models()
    assert catalog["schema"] == "nanojev.model-catalog.v1"
    assert len(catalog["models"]) == 1
    assert catalog["models"][0]["id"] == "nanojev-clef"
    assert set(catalog["models"][0]["operations"]) == {CLEF_EVALUATE, CLEF_PAIRWISE}
    assert catalog["models"][0]["checkpoint"]["release_manifest_sha256"] == SHA
    assert catalog["models"][0]["max_concurrent_inference"] == 1
    assert runtime.initializations == 1


def test_two_independent_workers_use_same_loaded_backend(real_http_backend):
    url, runtime, _, _ = real_http_backend
    worker_a = NanoJevSharedWorkerBackend(model="nanojev-clef", base_url=url)
    worker_b = NanoJevSharedWorkerBackend(model="nanojev-clef", base_url=url)
    assert worker_a.advertisement() == worker_b.advertisement()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker_a.execute_offer, offer(tag="one"))
        b = pool.submit(worker_b.execute_offer, offer(tag="two"))
        outcomes = [a.result(), b.result()]
    assert [r["response"]["model"] for r in outcomes] == ["nanojev-clef"] * 2
    assert [r["solution"]["model"]["release_manifest_sha256"] for r in outcomes] == [SHA] * 2
    assert runtime.calls == 2 and runtime.initializations == 1
    assert runtime.max_concurrent == 1  # serialized GPU head, no model clones


def test_wrong_model_fails_before_compute_and_cannot_be_advertised(real_http_backend):
    url, runtime, _, _ = real_http_backend
    worker = NanoJevSharedWorkerBackend(model="nanojev-clef", base_url=url)
    with pytest.raises(ModelContractError, match="cannot execute"):
        worker.execute_offer(offer(model="nanojev-other"))
    with pytest.raises(ModelContractError, match="cannot execute"):
        worker.execute_offer(offer(operation="chat.completions"))
    with pytest.raises(NanoJevProviderError, match="is not loaded"):
        NanoJevSharedWorkerBackend(model="nanojev-other", base_url=url)
    assert runtime.calls == 0


def test_changed_checkpoint_fails_before_inference(real_http_backend):
    url, runtime, _, _ = real_http_backend
    worker = NanoJevSharedWorkerBackend(model="nanojev-clef", base_url=url)
    runtime.release_manifest_sha256 = "b" * 64
    with pytest.raises(NanoJevProviderError, match="changed since Worker advertisement"):
        worker.execute_offer(offer())
    assert runtime.calls == 0


def test_service_rejects_mismatched_model_operation_and_checkpoint(real_http_backend):
    _, runtime, _, mod = real_http_backend
    valid = {"schema": mod.INFERENCE_REQUEST_SCHEMA, "model": "nanojev-clef",
             "operation": CLEF_EVALUATE, "checkpoint_sha256": SHA,
             "input": native_payload()}
    for mutation in ({"model": "nanojev-other"},
                     {"operation": "chat.completions"},
                     {"checkpoint_sha256": "b" * 64},
                     {"input": "not an object"},
                     {"schema": "old.infer.v0"}):
        with pytest.raises((ValueError, KeyError, TypeError)):
            runtime.infer({**valid, **mutation})
    assert runtime.calls == 0


def test_capacity_limit_rejects_busy_request_without_executing(real_http_backend):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    url, runtime, server, mod = real_http_backend
    permits = [server.inference_slots.acquire(blocking=False)
               for _ in range(mod.MAX_PENDING_INFERENCE)]
    assert all(permits)
    req = Request(url+"/api/infer", data=json.dumps({
        "schema": mod.INFERENCE_REQUEST_SCHEMA, "model": "nanojev-clef",
        "operation": CLEF_EVALUATE, "checkpoint_sha256": SHA,
        "input": native_payload(),
    }).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(req, timeout=2)
        assert caught.value.code == 429
        assert json.loads(caught.value.read())["error"] == "NANOJEV_INFERENCE_CAPACITY_EXHAUSTED"
    finally:
        for _ in permits:
            server.inference_slots.release()
    assert runtime.calls == 0


def test_lazy_manager_shares_one_backend_between_two_worker_clients(real_http_backend):
    from tools.nanojev_lifecycle_service import (
        NanoJevLifecycle, NanoJevManagerServer,
    )
    backend_url, runtime, _, _ = real_http_backend

    class ExistingBackend:
        def __init__(self):
            self.backend_url = backend_url
            self.started = False
            self.starts = 0
        def health(self):
            return self.started
        def start(self):
            self.starts += 1
            self.started = True
        def stop(self):
            self.started = False

    controller = ExistingBackend()
    lifecycle = NanoJevLifecycle(controller, idle_seconds=300)
    server = NanoJevManagerServer(("127.0.0.1", 0), lifecycle,
                                  proxy_timeout_seconds=10)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        manager_url = f"http://127.0.0.1:{server.server_port}"
        # Independent Worker adapters never instantiate their own model.
        with ThreadPoolExecutor(max_workers=2) as pool:
            def run_one(tag):
                worker = NanoJevSharedWorkerBackend(model="nanojev-clef", base_url=manager_url)
                return worker.execute_offer(offer(tag=tag))
            a = pool.submit(run_one, "one")
            b = pool.submit(run_one, "two")
            assert a.result()["response"]["model"] == "nanojev-clef"
            assert b.result()["response"]["model"] == "nanojev-clef"
        assert controller.starts == 1
        assert runtime.initializations == 1 and runtime.calls == 2
        assert runtime.max_concurrent == 1
        assert lifecycle.active_requests == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_existing_live_worker_refuses_unverified_nanojev_advertisement(real_http_backend):
    from main_computer.hub_cli_live_session import _nanojev_live_market
    url, runtime, _, _ = real_http_backend
    provider = NanoJevClefProvider(base_url=url)
    market = _nanojev_live_market(provider.solve, "nanojev-clef")
    assert market == {"models": ["nanojev-clef"],
                      "capabilities": [CLEF_EVALUATE, CLEF_PAIRWISE],
                      "checkpoint_sha256": SHA}
    runtime.release_manifest_sha256 = "b" * 64
    assert _nanojev_live_market(provider.solve, "nanojev-clef")["checkpoint_sha256"] == "b" * 64
    with pytest.raises(ValueError, match="bound, model-matching"):
        _nanojev_live_market(lambda **kwargs: {}, "nanojev-clef")
    with pytest.raises(ValueError, match="bound, model-matching"):
        _nanojev_live_market(provider.solve, "model-other")


def test_runtime_registry_supports_future_loaded_models_without_lying_about_production(real_http_backend):
    from main_computer.providers.nanojev import NanoJevModelClient
    url, clef, server, mod = real_http_backend

    class LoadedOtherModel:
        def __init__(self):
            self.calls = 0
        def model_catalog(self):
            return {"ready":True,"models":[{"id":"nanojev-other","ready":True,
                "operations":["nanojev.other.evaluate.v1"],
                "checkpoint":{"release_name":"other-test", "release_manifest_sha256":"b"*64}}]}
        def infer(self, request):
            if request.get("checkpoint_sha256") != "b"*64:
                raise ValueError("wrong checkpoint")
            if request.get("operation") != "nanojev.other.evaluate.v1":
                raise ValueError("unsupported operation")
            self.calls += 1
            model = {"family":"nanojev-other","release_name":"other-test",
                     "release_manifest_sha256":"b"*64}
            return {"schema":mod.INFERENCE_RESULT_SCHEMA,
                    "model":"nanojev-other", "operation":"nanojev.other.evaluate.v1",
                    "checkpoint":model,"result":{"model":model,"answer":{"value":11}}}

    fake_loaded_head = LoadedOtherModel()
    # The production ClefServer still registers ONLY CLEF; this test injects
    # a second *loaded runtime instance* to prove generic protocol scalability.
    server.loaded_models = mod.LoadedNanoJevModels({
        "nanojev-clef": clef, "nanojev-other": fake_loaded_head})
    worker = NanoJevSharedWorkerBackend(model="nanojev-other", base_url=url)
    assert worker.advertisement() == {
        "models":["nanojev-other"],"capabilities":["nanojev.other.evaluate.v1"],
        "checkpoint_sha256":"b"*64}
    selected = worker.execute_offer({"work":{
        "model":"nanojev-other", "operation":"nanojev.other.evaluate.v1",
        "capabilities":["nanojev.other.evaluate.v1"],
        "input":{"kind":"nanojev.other.evaluate.v1", "payload":{"question":11}},
    }})
    assert selected["response"]["model"] == "nanojev-other"
    assert selected["solution"]["result"]["answer"]["value"] == 11
    assert fake_loaded_head.calls == 1 and clef.calls == 0
    with pytest.raises(NanoJevProviderError, match="not loaded"):
        NanoJevModelClient(model="nanojev-not-loaded", base_url=url).solve(
            capability="nanojev.other.evaluate.v1", payload={"question":11})
