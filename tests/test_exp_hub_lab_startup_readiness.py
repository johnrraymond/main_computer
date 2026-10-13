"""Failure modes from a slow three-Hub Stable Hub lab startup."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from tools.exp_hub_lab import run_lab as exp_lab
from tools.stable_hub_lab import run_lab as stable_lab

SMOKE = Path("deploy/hub-topology/smoke-topology.json")


def test_each_hub_gets_a_complete_readiness_window(monkeypatch):
    # Child processes bind in parallel but become ready at staggered times.
    # Previously Hub 2 and 3 inherited Hub 1's nearly expired deadline.
    clock = [0.0]
    thresholds = {"smoke-hub1": 0.6, "smoke-hub2": 1.2, "smoke-hub3": 1.8}
    ports = {"8870": "smoke-hub1", "8871": "smoke-hub2", "8872": "smoke-hub3"}

    monkeypatch.setattr(stable_lab.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stable_lab.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration))

    def fetch(url, *, timeout):
        hub = next(value for port, value in ports.items() if f":{port}/" in url)
        if clock[0] < thresholds[hub]:
            raise OSError("still initializing")
        if url.endswith("/health"):
            return {"ok": True, "service": "main_computer.stable_hub"}
        return {
            "ok": True, "service": "main_computer.stable_hub",
            "hub_id": hub, "cluster_id": "main-computer-smoke-hub",
            "storage": {"namespace": "main-computer-hub-smoke"}, "contract": {},
        }
    monkeypatch.setattr(stable_lab, "_read_json_url", fetch)
    result = stable_lab.check_cluster(topology_path=SMOKE, timeout=1.0)
    assert result["ok"] is True, result
    assert [item["hub_id"] for item in result["checks"]] == list(thresholds)
    assert clock[0] >= 1.8


def test_http_404_identifies_url_and_response_body(monkeypatch):
    def fake_fetch(url, *, timeout):
        raise HTTPError(url, 404, "Not Found", {}, BytesIO(b'{"path":"/health","hub_id":"smoke-hub2"}'))
    clock = [0.0]
    monkeypatch.setattr(stable_lab.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stable_lab.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(stable_lab, "_read_json_url", fake_fetch)
    result = stable_lab.check_cluster(topology_path=SMOKE, timeout=0.11)
    assert result["ok"] is False
    assert len(result["checks"]) == 3
    assert "GET http://127.0.0.1:8871/health: HTTP 404" in result["checks"][1]["error"]
    assert "smoke-hub2" in result["checks"][1]["error"]


def test_identity_from_wrong_service_is_not_accepted(monkeypatch):
    def fake_fetch(url, *, timeout):
        if url.endswith("/health"):
            return {"ok": True, "service": "main_computer.stable_hub"}
        return {"ok": True, "service": "legacy-hub", "hub_id": "smoke-hub1", "cluster_id": "main-computer-smoke-hub"}
    clock = [0.0]
    monkeypatch.setattr(stable_lab.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stable_lab.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(stable_lab, "_read_json_url", fake_fetch)
    result = stable_lab.check_cluster(topology_path=SMOKE, timeout=0.11)
    assert result["ok"] is False
    assert "legacy-hub" in result["checks"][0]["error"]


def test_exp_lab_shows_cluster_errors_when_parity_blocked(monkeypatch, capsys):
    monkeypatch.setattr(
        "tools.exp_hub_lab.stable_parity.verify_stable_interfaces",
        lambda **kw: {"ok": False, "cluster": {
            "ok": False, "topology_path": str(SMOKE), "cluster_id": "main-computer-smoke-hub",
            "checks": [{"hub_id": "smoke-hub2", "hub_url": "http://127.0.0.1:8871", "error": "HTTP 404"}],
        }, "checks": []},
    )
    assert exp_lab.main(["--verify-interfaces"]) == 1
    output = capsys.readouterr().out
    assert "smoke-hub2" in output and "HTTP 404" in output
    assert "Start the smoke cluster" in output
