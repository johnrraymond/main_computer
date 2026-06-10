from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from main_computer.local_platform_lifecycle import (
    install_site,
    lifecycle_plan,
    verify_site,
    website_docker_action,
)
from main_computer.local_platform_registry import load_local_platform_registry
from main_computer.website_project_manifest import create_website_project, list_website_projects


def test_lifecycle_plan_uses_generated_compose_and_registry_aliases(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    plan = lifecycle_plan(tmp_path, "hub-site", lane="local-prod", action="start")

    assert plan.lane == "local"
    assert plan.registry_lane == "prod"
    assert plan.service == "hub-local"
    assert plan.port == 18080
    assert plan.url == "http://localhost:18080/"
    assert plan.compose_path == tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml"
    assert plan.command == [
        "docker",
        "compose",
        "-p",
        "main-computer-local-platform-unleashed",
        "-f",
        str(plan.compose_path),
        "up",
        "-d",
        "--build",
        "hub-local",
    ]


def test_install_registers_missing_site_allocates_ports_and_writes_compose(tmp_path: Path) -> None:
    create_website_project(tmp_path, "portfolio-site", "Portfolio Site")

    result = install_site(tmp_path, "portfolio-site")

    assert result["ok"] is True
    assert result["registered"] is True
    registry = load_local_platform_registry(tmp_path)
    assert registry.resolve("portfolio-site", "prod").port == 18100
    assert registry.resolve("portfolio-site", "dev").port == 18101
    assert registry.resolve("portfolio-site", "prod").service == "portfolio-site-prod"
    assert registry.resolve("portfolio-site", "dev").service == "portfolio-site-dev"

    compose_path = tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml"
    compose_text = compose_path.read_text(encoding="utf-8")
    assert "portfolio-site-prod:" in compose_text
    assert "portfolio-site-dev:" in compose_text
    assert '- "0.0.0.0:18100:8080"' in compose_text
    assert '- "0.0.0.0:18101:8080"' in compose_text


def test_start_dry_run_returns_docker_command_without_running_docker(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    result = website_docker_action(tmp_path, "start", "hub-site", lane="dev", dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["service"] == "hub-dev"
    assert result["plan"]["compose_scope"] == "aggregate"
    assert result["plan"]["command"][-4:] == ["up", "-d", "--build", "hub-dev"]


def test_lifecycle_plan_can_opt_into_site_scoped_compose(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    plan = lifecycle_plan(tmp_path, "hub-site", lane="dev", action="start", compose_scope="site")

    assert plan.compose_scope == "site"
    assert plan.compose_project == "main-computer-website-hub-site"
    assert plan.compose_path == (
        tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "local-platform" / "docker-compose.yml"
    )
    assert plan.command == [
        "docker",
        "compose",
        "-p",
        "main-computer-website-hub-site",
        "-f",
        str(plan.compose_path),
        "up",
        "-d",
        "--build",
        "hub-dev",
    ]


def test_site_scoped_start_dry_run_writes_only_selected_site_compose(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    result = website_docker_action(tmp_path, "start", "hub-site", lane="dev", dry_run=True, compose_scope="site")

    site_compose_path = tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "local-platform" / "docker-compose.yml"
    aggregate_compose_path = tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml"
    site_text = site_compose_path.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["compose_scope"] == "site"
    assert result["plan"]["compose_project"] == "main-computer-website-hub-site"
    assert result["plan"]["compose_path"] == str(site_compose_path)
    assert result["plan"]["command"][3] == "main-computer-website-hub-site"
    assert result["plan"]["command"][5] == str(site_compose_path)
    assert 'name: "main-computer-website-hub-site"' in site_text
    assert "hub-local:" in site_text
    assert "hub-dev:" in site_text
    assert "blog-local:" not in site_text
    assert "blog-dev:" not in site_text
    assert not aggregate_compose_path.exists()


def test_publish_runs_generated_compose_verifies_and_marks_manifest(tmp_path: Path, monkeypatch) -> None:
    list_website_projects(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="started\n", stderr="")

    def fake_wait(url: str, timeout_s: float):
        return {
            "ok": True,
            "status": 200,
            "body": '{"ok": true}',
            "payload": {"ok": True, "site_id": "hub-site", "site_lane": "dev"},
            "attempts": 1,
        }

    monkeypatch.setattr("main_computer.local_platform_lifecycle.subprocess.run", fake_run)
    monkeypatch.setattr("main_computer.local_platform_lifecycle._wait_for_status_url", fake_wait)

    result = website_docker_action(tmp_path, "publish", "hub-site", lane="dev")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["verify_status"] == 200
    assert calls
    assert calls[0][-4:] == ["up", "-d", "--build", "hub-dev"]
    assert any("docker-compose.websites.yml" in part for part in calls[0])

    manifest = json.loads((tmp_path / "runtime" / "websites" / "hub-site" / "site.json").read_text(encoding="utf-8"))
    assert manifest["local_platform"]["lanes"]["dev"]["last_publish_verified"] is True
    assert manifest["local_platform"]["lanes"]["dev"]["last_published_service"] == "hub-dev"
    assert manifest["local_platform"]["lanes"]["dev"]["last_published_url"] == "http://localhost:18082/"


def test_stop_and_logs_dry_runs_target_only_selected_service(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    stop_result = website_docker_action(tmp_path, "stop", "blog-site", lane="production", dry_run=True)
    logs_result = website_docker_action(tmp_path, "logs", "blog-site", lane="dev", dry_run=True, tail=50)

    assert stop_result["plan"]["service"] == "blog-local"
    assert stop_result["plan"]["command"][-2:] == ["stop", "blog-local"]
    assert logs_result["plan"]["service"] == "blog-dev"
    assert logs_result["plan"]["command"][-4:] == ["logs", "--tail", "50", "blog-dev"]


def test_verify_uses_status_url_without_docker(tmp_path: Path, monkeypatch) -> None:
    list_website_projects(tmp_path)

    def fake_wait(url: str, timeout_s: float):
        assert url == "http://localhost:18082/api/site/status"
        return {
            "ok": True,
            "status": 200,
            "body": '{"ok": true}',
            "payload": {"ok": True},
            "attempts": 1,
        }

    monkeypatch.setattr("main_computer.local_platform_lifecycle._wait_for_status_url", fake_wait)

    result = verify_site(tmp_path, "hub-site", lane="dev")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["verify_status"] == 200


def test_website_docker_cli_dry_run_outputs_json(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    script = Path("tools") / "local-platform" / "website-docker.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "start",
            "hub-site",
            "--lane",
            "dev",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["plan"]["service"] == "hub-dev"
    assert payload["plan"]["command"][-1] == "hub-dev"


def test_website_docker_cli_site_scope_dry_run_outputs_site_compose_plan(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    script = Path("tools") / "local-platform" / "website-docker.py"
    site_compose_path = (
        tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "local-platform" / "docker-compose.yml"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "start",
            "hub-site",
            "--lane",
            "dev",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
            "--compose-scope",
            "site",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["plan"]["compose_scope"] == "site"
    assert payload["plan"]["compose_project"] == "main-computer-website-hub-site"
    assert payload["plan"]["compose_path"] == str(site_compose_path)
    assert payload["plan"]["command"][3] == "main-computer-website-hub-site"
    assert payload["plan"]["command"][5] == str(site_compose_path)
