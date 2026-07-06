from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_PATH = REPO_ROOT / "scripts" / "smoke_openclaw_persistence_pushback.py"


def load_pushback_smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_openclaw_persistence_pushback_test", SMOKE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pushback_smoke_script_exists_and_documents_roundtrip() -> None:
    text = SMOKE_PATH.read_text(encoding="utf-8")
    assert "openclaw-persistence-pushback" in text
    assert "extract/edit/apply/readback" in text or "exported JSON text payload was edited automatically" in text
    assert "expected-current SHA" in text
    assert "container_probe" in text
    assert "--restart-container" in text
    assert "MAIN_COMPUTER_PUSHBACK_MARKER" in text
    assert "memory/**/*.md" in text


def test_pushback_smoke_self_test_runs_without_docker() -> None:
    completed = subprocess.run(
        [sys.executable, str(SMOKE_PATH), "--self-test", "--json"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    result = payload["result"]
    assert result["smoke"] == "openclaw-persistence-pushback"
    assert result["apply_stats"]["changed"] == 1
    assert result["dry_run_stats"]["file_count"] == 1
    assert "fresh high-fidelity re-extraction" in " ".join(result["proved"])
    assert result["marker"].startswith("MC_OPENCLAW_PUSHBACK_SMOKE_")
    assert result["target_relative_path"] == "memory/self-test.md"


def test_container_probe_uses_plain_docker_exec_without_compose_t_flag(monkeypatch) -> None:
    module = load_pushback_smoke_module()
    captured: dict[str, list[str]] = {}

    def fake_run_docker_exec(args: list[str], *, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps({"target": "/workspace/memory/file.md", "marker": "MC_MARKER"}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(module, "run_docker_exec", fake_run_docker_exec)

    result = module.container_probe(
        container="openclaw-gateway",
        marker="MC_MARKER",
        relative_path="memory/file.md",
        container_workspace="/workspace",
        timeout_s=1.0,
    )

    args = captured["args"]
    assert result["marker"] == "MC_MARKER"
    assert args[:2] == ["docker", "exec"]
    assert "-T" not in args
    assert "-t" not in args
    assert args[2:8] == [
        "-e",
        "MAIN_COMPUTER_PUSHBACK_MARKER=MC_MARKER",
        "-e",
        "MAIN_COMPUTER_PUSHBACK_RELATIVE_PATH=memory/file.md",
        "-e",
        "MAIN_COMPUTER_PUSHBACK_CONTAINER_WORKSPACE=/workspace",
    ]
    assert args[8:] == ["openclaw-gateway", "node", "-e", args[-1]]

