from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "local-platform" / "debug-website.py"


def isolated_local_platform_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "MAIN_COMPUTER_LOCAL_PLATFORM_REGISTRY_PATH",
        "MAIN_COMPUTER_LOCAL_PLATFORM_BUILTIN_PORT_START",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END",
        "MAIN_COMPUTER_LOCAL_PLATFORM_COMPOSE_PROJECT",
        "MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_COMPOSE_PATH",
    ):
        env.pop(name, None)
    return env


def run_debug_website(*args: str, repo_root: Path) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), *args, "--repo-root", str(repo_root)],
        text=True,
        capture_output=True,
        check=False,
        env=isolated_local_platform_env(),
    )
    payload = json.loads(completed.stdout)
    return completed.returncode, payload


def test_debug_website_deployer_ensures_bootstrap_site_and_registry(tmp_path: Path) -> None:
    returncode, payload = run_debug_website(
        "ensure",
        "--site",
        "debug-bootstrap",
        "--bootstrap",
        repo_root=tmp_path,
    )

    assert returncode == 0, payload
    assert payload["ok"] is True
    assert payload["site_id"] == "debug-bootstrap"
    assert payload["bootstrap"] is True
    assert payload["repo_relative_path"] == "runtime/websites/debug-bootstrap"
    assert payload["registry"]["created"] is True
    assert payload["registry"]["ports"]["prod"] == 18100
    assert payload["registry"]["ports"]["dev"] == 18101
    assert payload["compose"]["ok"] is True

    site_dir = tmp_path / "runtime" / "websites" / "debug-bootstrap"
    assert (site_dir / "site.json").exists()
    assert (site_dir / "index.html").exists()
    assert (site_dir / "style.css").exists()
    assert (site_dir / "script.js").exists()
    assert (site_dir / "builder.json").exists()

    manifest = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "debug-bootstrap"
    assert manifest["kind"] == "debug-site"
    assert manifest["schema_version"] == 2
    assert manifest["site_model"] == "2.0"
    assert manifest["source"] == {
        "kind": "host_runtime_site",
        "path": "runtime/websites/debug-bootstrap",
    }
    assert manifest["artifacts"]["required_files"] == ["site.json", "index.html", "style.css", "script.js", "builder.json"]
    assert manifest["debug"]["bootstrap"] is True
    assert manifest["debug"]["managed_by"] == "tools/local-platform/debug-website.py"
    assert "generate, repair, and debug websites safely" in (site_dir / "index.html").read_text(encoding="utf-8")

    registry = json.loads((tmp_path / "runtime" / "local-platform" / "sites.json").read_text(encoding="utf-8"))
    debug_site = registry["sites"]["debug-bootstrap"]
    assert debug_site["kind"] == "debug-site"
    assert debug_site["repo_relative_path"] == "runtime/websites/debug-bootstrap"
    assert debug_site["lanes"]["prod"]["service"] == "debug-bootstrap-prod"

    compose_text = (tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml").read_text(
        encoding="utf-8"
    )
    assert "debug-bootstrap-prod:" in compose_text
    assert "debug-bootstrap-dev:" in compose_text
    assert 'SITE_ID: "debug-bootstrap"' in compose_text


def test_debug_website_deployer_is_idempotent_and_reports_status_and_list(tmp_path: Path) -> None:
    first_returncode, first = run_debug_website(
        "ensure",
        "--site",
        "debug-bootstrap",
        "--bootstrap",
        repo_root=tmp_path,
    )
    second_returncode, second = run_debug_website(
        "ensure",
        "--site",
        "debug-bootstrap",
        "--bootstrap",
        repo_root=tmp_path,
    )

    assert first_returncode == 0, first
    assert second_returncode == 0, second
    assert first["registry"]["created"] is True
    assert second["registry"]["created"] is False
    assert first["registry"]["ports"] == second["registry"]["ports"]

    status_returncode, status = run_debug_website(
        "status",
        "--site",
        "debug-bootstrap",
        repo_root=tmp_path,
    )
    assert status_returncode == 0, status
    assert status["ok"] is True
    assert status["site_exists"] is True
    assert status["registered"] is True
    assert status["manifest"]["debug"]["bootstrap"] is True

    list_returncode, listing = run_debug_website(
        "list",
        repo_root=tmp_path,
    )
    assert list_returncode == 0, listing
    assert listing["ok"] is True
    assert [site["id"] for site in listing["sites"]] == ["debug-bootstrap"]


def test_debug_website_deployer_supports_unique_purpose_sites(tmp_path: Path) -> None:
    returncode, payload = run_debug_website(
        "ensure",
        "--purpose",
        "zip git",
        "--unique",
        "--no-compose",
        repo_root=tmp_path,
    )

    assert returncode == 0, payload
    assert payload["ok"] is True
    assert payload["site_id"].startswith("debug-zip-git-")
    assert payload["bootstrap"] is False
    assert payload["compose"] is None

    site_dir = Path(payload["site_path"])
    manifest = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))
    assert manifest["site_model"] == "2.0"
    assert manifest["source"]["path"] == f"runtime/websites/{payload['site_id']}"
    assert manifest["debug"]["purpose"] == "zip-git"
    assert manifest["debug"]["disposable"] is True


def test_debug_website_deployer_rejects_non_debug_or_unsafe_site_ids(tmp_path: Path) -> None:
    for bad_site_id in ("blog-site", "../debug-escape", "debug-", "debug_bad"):
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(SCRIPT),
                "ensure",
                "--site",
                bad_site_id,
                "--repo-root",
                str(tmp_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=isolated_local_platform_env(),
        )
        assert completed.returncode == 2
        payload = json.loads(completed.stdout)
        assert payload["ok"] is False
        assert "Debug website id" in payload["error"]
