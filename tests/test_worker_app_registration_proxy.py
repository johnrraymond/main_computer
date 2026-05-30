from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.viewport_server import ViewportServer


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    assert isinstance(data, dict)
    return data


def test_worker_app_proxy_registers_phase12_seller_offer_with_hub() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub_config = MainComputerConfig(
            workspace=root / "workspace",
            hub_root=root / "hub",
            hub_allow_insecure_dev_network=False,
        )
        hub = HubHttpServer(("127.0.0.1", 0), hub_config, verbose=False)
        hub_thread = threading.Thread(target=hub.serve_forever, daemon=True)
        hub_thread.start()

        hub_url = f"http://127.0.0.1:{hub.server_port}"
        viewport_config = MainComputerConfig(workspace=root / "workspace", hub_url=hub_url)
        viewport = ViewportServer(("127.0.0.1", 0), viewport_config, verbose=False)
        viewport_thread = threading.Thread(target=viewport.serve_forever, daemon=True)
        viewport_thread.start()

        try:
            payload = {
                "hub_url": hub_url,
                "worker": {
                    "node_id": "phase12-ui-worker-001",
                    "endpoint": "http://127.0.0.1:8771",
                    "model": "mock-ai-model-phase12",
                    "models": ["mock-ai-model-phase12"],
                    "credits_per_request": 5500123,
                    "max_concurrency": 1,
                    "pricing": {
                        "pricing_type": "fixed_per_call_v0",
                        "credits_per_request": 5500123,
                        "unit": "compute_credit",
                    },
                    "execution": {
                        "mode": "worker_pull_v0",
                        "max_concurrency": 1,
                    },
                    "capabilities": {
                        "capabilities": ["chat.completions"],
                    },
                },
            }
            viewport_url = f"http://127.0.0.1:{viewport.server_port}"
            data = _post_json(f"{viewport_url}/api/applications/worker/register-offer", payload)

            assert data["ok"] is True
            worker = data["worker"]
            assert isinstance(worker, dict)
            assert worker["node_id"] == "phase12-ui-worker-001"
            assert worker["models"] == ["mock-ai-model-phase12"]
            assert worker["credits_per_request"] == 5500123
            assert worker["max_concurrency"] == 1

            offer = data["offer"]
            assert isinstance(offer, dict)
            assert offer["worker_node_id"] == "phase12-ui-worker-001"
            assert offer["pricing_type"] == "fixed_per_call_v0"
            assert offer["credits_per_request"] == 5500123
            assert offer["unit"] == "compute_credit"
            assert offer["execution_mode"] == "worker_pull_v0"
            assert offer["price_source"] == "worker_registration"

            health = _post_json(f"{viewport_url}/api/applications/worker/hub-health", {"hub_url": hub_url})
            assert health["ok"] is True
            assert health["reachable"] is True
        finally:
            viewport.shutdown()
            hub.shutdown()
            viewport.server_close()
            hub.server_close()
