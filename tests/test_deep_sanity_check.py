from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path, PureWindowsPath


REPO_ROOT = Path(__file__).resolve().parents[1]


def fixture_windows_root(name: str | None = None) -> PureWindowsPath:
    repo_name = name or REPO_ROOT.name
    return PureWindowsPath("C:/main-computer-fixtures") / "dsl" / repo_name


def fixture_wsl_root(name: str | None = None) -> str:
    repo_name = name or REPO_ROOT.name
    return f"/mnt/c/main-computer-fixtures/dsl/{repo_name}"


def load_tool():
    script_path = REPO_ROOT / "tools" / "deep_sanity_check.py"
    spec = importlib.util.spec_from_file_location("deep_sanity_check", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def minimal_repo(root: Path) -> None:
    (root / "new_patch.py").write_text("# marker\n", encoding="utf-8")
    (root / "main_computer").mkdir()
    (root / "main_computer" / "__init__.py").write_text("", encoding="utf-8")
    (root / "deploy" / "local-platform" / "generated").mkdir(parents=True)
    (root / "deploy" / "local-platform").mkdir(parents=True, exist_ok=True)
    (root / "deploy" / "coolify" / "local-docker").mkdir(parents=True)
    (root / "deploy" / "coolify" / "local-docker" / "docker-compose.yml").write_text(
        """
services:
  coolify:
    image: ghcr.io/coollabsio/coolify:latest
    ports:
      - "127.0.0.1:18000:8080"
""".lstrip(),
        encoding="utf-8",
    )


def test_parse_compose_services_extracts_site_ports_and_labels(tmp_path: Path) -> None:
    module = load_tool()
    compose = tmp_path / "docker-compose.websites.yml"
    compose.write_text(
        """
services:
  alpha-prod:
    image: main-computer-site-alpha-prod:latest
    environment:
      SITE_ID: "alpha"
      SITE_NAME: "Alpha"
      SITE_KIND: "static-site"
      SITE_LANE: "local"
    labels:
      - "traefik.http.routers.alpha.rule=Host(`alpha.example.com`)"
      traefik.http.services.alpha.loadbalancer.server.port: "8080"
    ports:
      - "0.0.0.0:18100:8080"
    volumes:
      - "../../../runtime/websites:/app/runtime/websites:ro"
""".lstrip(),
        encoding="utf-8",
    )

    services = module.parse_compose_services(compose, tmp_path)

    assert sorted(services) == ["alpha-prod"]
    service = services["alpha-prod"]
    assert service["environment"]["SITE_ID"] == "alpha"
    assert service["environment"]["SITE_LANE"] == "local"
    assert service["ports"] == [
        {
            "raw": '"0.0.0.0:18100:8080"',
            "host_ip": "0.0.0.0",
            "host_port": 18100,
            "container_port": 8080,
            "protocol": "tcp",
        }
    ]
    assert service["labels"]["traefik.http.services.alpha.loadbalancer.server.port"] == "8080"


def test_deep_report_flags_port_drift_remote_target_and_public_env(monkeypatch, tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)

    write_json(
        tmp_path / "runtime" / "local-platform" / "sites.json",
        {
            "schema_version": 1,
            "sites": {
                "alpha": {
                    "id": "alpha",
                    "name": "Alpha",
                    "kind": "static-site",
                    "repo_relative_path": "runtime/websites/alpha",
                    "lanes": {
                        "prod": {
                            "service": "alpha-prod",
                            "port": 18100,
                            "url": "http://localhost:18100/",
                            "status_url": "http://localhost:18100/api/site/status",
                        }
                    },
                }
            },
        },
    )
    site_dir = tmp_path / "runtime" / "websites" / "alpha"
    site_dir.mkdir(parents=True)
    write_json(
        site_dir / "site.json",
        {
            "id": "alpha",
            "name": "Alpha",
            "kind": "static-site",
            "builder": {
                "entry_html": "index.html",
                "stylesheet": "style.css",
                "script": "script.js",
                "state_file": "builder.json",
            },
            "local_platform": {
                "lanes": {
                    "local": {
                        "service": "alpha-prod",
                        "port": 18101,
                        "url": "http://localhost:18101/",
                        "status_url": "http://localhost:18101/api/site/status",
                    }
                }
            },
            "publish_targets": {
                "remote_prod": {
                    "controller_id": "local-coolify",
                    "project": "alpha",
                    "environment": "production",
                    "accepted_at": "2026-05-15T12:00:00Z",
                    "domain": "",
                }
            },
        },
    )
    for filename in ["index.html", "style.css", "script.js", "builder.json"]:
        (site_dir / filename).write_text("{}\n" if filename.endswith(".json") else "ok\n", encoding="utf-8")
    (site_dir / ".env").write_text("COOLIFY_TOKEN=should-not-be-public\n", encoding="utf-8")

    (tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml").write_text(
        """
services:
  alpha-prod:
    image: main-computer-site-alpha-prod:latest
    environment:
      SITE_ID: "alpha"
      SITE_NAME: "Alpha"
      SITE_KIND: "static-site"
      SITE_LANE: "local"
    ports:
      - "0.0.0.0:18102:8080"
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "docker_inspect_containers", lambda: ([], []))
    monkeypatch.setattr(module, "collect_local_port_owners", lambda: {})
    args = argparse.Namespace(
        repo_root=str(tmp_path),
        no_probe=True,
        no_wsl=True,
        probe_timeout=0.1,
        command_timeout=0.1,
    )

    report = module.build_report(args)
    messages = [finding["message"] for finding in report["findings"]]

    assert report["overall_status"] == "FAIL"
    assert "Website lane has mismatched intended ports across registry/manifest/compose." in messages
    assert "Public website tree contains a .env file." in messages
    assert "Accepted remote website target has no domain recorded; website-only deploys can look healthy locally but route nowhere remotely." in messages
    assert {row["port"] for row in report["website_port_matrix"] if row["site_id"] == "alpha"} == {18100, 18101, 18102}


def test_parse_wsl_list_handles_default_marker_and_states() -> None:
    module = load_tool()
    text = """
  NAME                   STATE           VERSION
* Ubuntu-22.04           Running         2
  docker-desktop         Stopped         2
"""

    distros = module.parse_wsl_list(text)

    assert distros == [
        {"name": "Ubuntu-22.04", "state": "Running", "version": "2", "default": True},
        {"name": "docker-desktop", "state": "Stopped", "version": "2", "default": False},
    ]


def test_redact_mapping_hides_secret_values() -> None:
    module = load_tool()

    redacted = module.redact_mapping({"COOLIFY_TOKEN": "abc123", "NORMAL_VALUE": "visible"})

    assert redacted["COOLIFY_TOKEN"] == "<redacted:6 chars>"
    assert redacted["NORMAL_VALUE"] == "visible"


def test_parse_netstat_ports_only_keeps_listeners_and_dedupes() -> None:
    module = load_tool()
    output = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:18101          0.0.0.0:0              LISTENING       79312
  TCP    0.0.0.0:18101          0.0.0.0:0              LISTENING       79312
  TCP    127.0.0.1:18101        127.0.0.1:62000        ESTABLISHED     0
"""

    owners = module.parse_netstat_ports(output)

    assert owners == {
        18101: [
            {"source": "netstat", "proto": "tcp", "local": "0.0.0.0:18101", "pid": "79312"},
        ]
    }


def test_classifies_docker_container_against_project_markers(tmp_path: Path) -> None:
    module = load_tool()
    container = {
        "Id": "abcdef1234567890",
        "Config": {
            "Image": "main-computer-site-alpha-dev:latest",
            "Labels": {
                "com.docker.compose.project": "main-computer-local-platform",
                "com.docker.compose.service": "alpha-dev",
            },
        },
        "State": {"Running": True, "Status": "running"},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18101"}]}},
        "Mounts": [{"Type": "bind", "Source": str(tmp_path), "Destination": "/app"}],
        "Name": "/main-computer-local-platform-alpha-dev-1",
    }

    summary = module.docker_container_summary(
        container,
        repo_root=tmp_path,
        website_matrix=[{"site_id": "alpha", "service": "alpha-dev"}],
        size_lookup={"main-computer-local-platform-alpha-dev-1": {"Size": "1.2MB"}},
    )

    assert summary["project_classification"]["is_project"] is True
    assert summary["project_classification"]["relation"] == "project"
    assert summary["host_ports"][0]["host_port"] == 18101
    assert summary["size"] == "1.2MB"


def test_classifies_wsl_docker_rows_as_project_or_not(tmp_path: Path) -> None:
    module = load_tool()

    project = module.classify_wsl_docker_row(
        {"Names": "main-computer-local-platform-hub-dev-1", "Image": "main-computer-site-hub-dev:latest"},
        tmp_path,
        [{"site_id": "hub-site", "service": "hub-dev"}],
    )
    other = module.classify_wsl_docker_row(
        {"Names": "unrelated-postgres", "Image": "postgres:16"},
        tmp_path,
        [{"site_id": "hub-site", "service": "hub-dev"}],
    )

    assert project["is_project"] is True
    assert project["relation"] == "project"
    assert other["is_project"] is False
    assert other["relation"] == "not-project"


def test_resolves_onlyoffice_compose_default_port(tmp_path: Path) -> None:
    module = load_tool()
    service = {
        "ports": [
            {
                "raw": '"127.0.0.1:${MAIN_COMPUTER_ONLYOFFICE_PORT:-18085}:80"',
            }
        ]
    }

    ports = module.compose_service_default_host_ports(service, env={})

    assert ports[0]["host_port"] == 18085
    assert ports[0]["container_port"] == 80


def test_collect_onlyoffice_state_flags_foreign_repo_and_stale_container(tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)
    (tmp_path / "docker-compose.onlyoffice.yml").write_text(
        """
services:
  onlyoffice:
    image: onlyoffice/documentserver:${MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG:-latest}
    ports:
      - "127.0.0.1:${MAIN_COMPUTER_ONLYOFFICE_PORT:-18085}:80"
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "tools" / "onlyoffice").mkdir(parents=True)
    (tmp_path / "tools" / "onlyoffice" / "check-onlyoffice.py").write_text("# checker\n", encoding="utf-8")
    (tmp_path / "tools" / "onlyoffice" / "onlyoffice-control.ps1").write_text("# control\n", encoding="utf-8")

    running = {
        "id": "aaa",
        "name": "main-computer-onlyoffice-debug-onlyoffice-1",
        "image": "onlyoffice/documentserver:latest",
        "running": True,
        "status": "running",
        "labels": {
            "com.docker.compose.project": "main-computer-onlyoffice-debug",
            "com.docker.compose.project.working_dir": str(fixture_windows_root("MainComputer")),
            "com.docker.compose.project.config_files": str(fixture_windows_root("MainComputer") / "docker-compose.onlyoffice.yml"),
        },
        "host_ports": [{"host_ip": "127.0.0.1", "host_port": 28084, "container_port": "80/tcp"}],
        "size": "859MB (virtual 4.93GB)",
        "project_classification": {"is_project": True, "relation": "project"},
    }
    stale = {
        "id": "bbb",
        "name": "main-computer-onlyoffice-onlyoffice-1",
        "image": "onlyoffice/documentserver:latest",
        "running": False,
        "status": "created",
        "labels": {
            "com.docker.compose.project": "main-computer-onlyoffice",
            "com.docker.compose.project.working_dir": str(fixture_windows_root("MainComputer")),
            "com.docker.compose.project.config_files": str(fixture_windows_root("MainComputer") / "docker-compose.onlyoffice.yml"),
        },
        "host_ports": [],
        "size": "4.1kB (virtual 4.07GB)",
        "project_classification": {"is_project": True, "relation": "project"},
    }
    findings: list = []

    state = module.collect_onlyoffice_state(
        tmp_path,
        [running, stale],
        {},
        {28084: [{"source": "docker", "container": "main-computer-onlyoffice-debug-onlyoffice-1", "image": "onlyoffice/documentserver:latest"}]},
        findings,
        probe=False,
        probe_timeout_s=0.1,
    )

    messages = [finding.message for finding in findings]
    assert state["expected_ports"][0]["host_port"] == 18085
    assert state["running_containers"] == ["main-computer-onlyoffice-debug-onlyoffice-1"]
    assert state["stopped_or_created_containers"] == ["main-computer-onlyoffice-onlyoffice-1"]
    assert "ONLYOFFICE is running, but not on the expected current-repo port." in messages
    assert "Stopped/created ONLYOFFICE container(s) exist and may be stale deploy leftovers." in messages
    assert "Multiple ONLYOFFICE Compose projects exist." in messages
    assert "ONLYOFFICE container(s) point at a different repository path than this sanity-check repo." in messages


def test_wsl_probe_script_is_posix_sh_syntax_safe(tmp_path: Path) -> None:
    module = load_tool()
    script = module.build_wsl_probe_script("main_computer_test")

    assert "printf '%s\n'" not in script
    assert "for p in \\" in script

    import subprocess

    result = subprocess.run(["sh", "-n"], input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr


def test_collect_onlyoffice_state_ignores_netstat_echo_when_docker_owner_is_onlyoffice(tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)
    (tmp_path / "docker-compose.onlyoffice.yml").write_text(
        """
services:
  onlyoffice:
    image: onlyoffice/documentserver:${MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG:-latest}
    ports:
      - "127.0.0.1:28084:80"
""".lstrip(),
        encoding="utf-8",
    )

    running = {
        "id": "aaa",
        "name": "main-computer-onlyoffice-debug-onlyoffice-1",
        "image": "onlyoffice/documentserver:latest",
        "running": True,
        "status": "running",
        "labels": {
            "com.docker.compose.project": "main-computer-onlyoffice-debug",
            "com.docker.compose.project.working_dir": str(tmp_path),
            "com.docker.compose.project.config_files": str(tmp_path / "docker-compose.onlyoffice.yml"),
        },
        "host_ports": [{"host_ip": "127.0.0.1", "host_port": 28084, "container_port": "80/tcp"}],
        "project_classification": {"is_project": True, "relation": "project"},
    }
    findings: list = []

    module.collect_onlyoffice_state(
        tmp_path,
        [running],
        {28084: [{"source": "netstat", "proto": "tcp", "local": "127.0.0.1:28084", "pid": "79312"}]},
        {28084: [{"source": "docker", "container": "main-computer-onlyoffice-debug-onlyoffice-1", "image": "onlyoffice/documentserver:latest"}]},
        findings,
        probe=False,
        probe_timeout_s=0.1,
    )

    messages = [finding.message for finding in findings]
    assert "Expected ONLYOFFICE port has non-ONLYOFFICE owner(s)." not in messages
    assert "Expected ONLYOFFICE port is occupied but no ONLYOFFICE Docker owner was found." not in messages


def test_collect_machine_environments_groups_foreign_active_deploy(monkeypatch, tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)
    foreign_root = tmp_path.parent / "main_computer_debug14"
    foreign_root.mkdir()
    (foreign_root / "new_patch.py").write_text("# marker\n", encoding="utf-8")

    monkeypatch.setenv("MAIN_COMPUTER_DEBUG_ROOT", str(foreign_root))
    monkeypatch.setattr(module, "collect_host_project_processes", lambda timeout_s: ([], None))

    docker_rows = [
        {
            "name": "mc-coolify-main-computer-test-debug-f4029a0d",
            "image": "ghcr.io/coollabsio/coolify:latest",
            "running": True,
            "status": "running",
            "labels": {
                "com.docker.compose.project": "main-computer-coolify-main-computer-test-debug-f4029a0d",
                "com.docker.compose.project.working_dir": str(foreign_root),
                "com.docker.compose.project.config_files": str(foreign_root / "deploy" / "coolify" / "local-docker" / "docker-compose.yml"),
                "com.docker.compose.service": "coolify",
            },
            "host_ports": [{"host_ip": "127.0.0.1", "host_port": 27066, "container_port": "8080/tcp"}],
            "project_classification": {"is_project": True, "relation": "project"},
        }
    ]
    findings: list = []

    state = module.collect_machine_environments(
        tmp_path,
        {"env": {}},
        docker_rows,
        {},
        {},
        {"distros": []},
        findings,
        timeout_s=0.1,
    )

    roots = {row["root"]: row for row in state["roots"]}
    assert str(foreign_root) in roots
    assert roots[str(foreign_root)]["running_deploy_count"] == 1
    assert roots[str(foreign_root)]["sanity"]["status"] == "WARN"
    assert "non-current environment has running deploy/container(s)" in roots[str(foreign_root)]["sanity"]["issues"]
    assert any(f.message == "Running Main Computer-related deploys exist outside the current checkout." for f in findings)


def test_env_root_from_paths_normalizes_wsl_and_compose_paths(tmp_path: Path) -> None:
    module = load_tool()
    debug_root = fixture_windows_root("main_computer_debug14")
    repo_root = fixture_windows_root()

    assert module.env_root_from_path(str(debug_root / "deploy" / "coolify" / "local-docker" / "docker-compose.yml")) == debug_root.as_posix()
    assert module.env_root_from_path(f"{fixture_wsl_root()}/new_patch.py") == repo_root.as_posix()


def test_machine_path_extraction_ignores_urls_and_metadata_roots() -> None:
    module = load_tool()
    repo_root = fixture_windows_root()

    assert module.extract_path_candidates("https://github.com/directus/directus") == []
    assert module.extract_path_candidates("http://127.0.0.1:28084") == []
    assert module.extract_path_candidates("https://serversideup.net/open-source/docker-php/docs/") == []
    assert module.extract_path_candidates(str(repo_root / "deploy" / "coolify" / "local-docker" / "docker-compose.yml")) == [
        repo_root.as_posix()
    ]


def test_wsl_docker_desktop_client_access_is_not_project_distro() -> None:
    module = load_tool()
    record = {
        "name": "Ubuntu",
        "state": "Running",
        "project_paths": [],
        "processes": [],
        "probe_returncode": 0,
        "probe_timed_out": False,
        "docker_containers": [
            {
                "Names": "main-computer-local-platform-hub-dev-1",
                "Labels": f"desktop.docker.io/binds/0/Source={fixture_windows_root() / 'runtime' / 'websites'},com.docker.compose.project.working_dir={fixture_windows_root()}",
                "classification": {"is_project": True, "relation": "project"},
            }
        ],
    }

    cls = module.classify_wsl_distro(record, Path(str(fixture_windows_root())))

    assert cls["is_project"] is False
    assert cls["relation"] == "docker-client-project-access"
    assert "Docker CLI in this distro can see" in cls["reasons"][0]


def test_docker_desktop_wsl_stays_docker_system_even_with_project_process() -> None:
    module = load_tool()
    record = {
        "name": "docker-desktop",
        "state": "Running",
        "project_paths": [],
        "processes": ["123 python main_computer worker"],
        "probe_returncode": 0,
        "probe_timed_out": False,
        "docker_containers": [],
    }

    cls = module.classify_wsl_distro(record, Path(str(fixture_windows_root())))

    assert cls["is_project"] is False
    assert cls["relation"] == "docker-system"

def test_env_root_collapses_repo_child_executables_and_runtime_paths() -> None:
    module = load_tool()
    debug_root = fixture_windows_root("main_computer_debug")
    repo_root = fixture_windows_root()

    assert module.extract_path_candidates(r"C:\WINDOWS\system32\wsl.exe") == []
    assert module.env_root_from_path(
        str(debug_root / ".main-computer" / "instances" / "maincomputer" / "debug" / "venv" / "Scripts" / "python.exe")
    ) == debug_root.as_posix()
    assert module.env_root_from_path(
        str(repo_root / ".proto-dev" / "runtime" / "onlyoffice" / "workbooks")
    ) == repo_root.as_posix()


def test_machine_environment_collapses_current_repo_child_roots(monkeypatch, tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)

    monkeypatch.setenv("MAIN_COMPUTER_ONLYOFFICE_STORAGE_ROOT", str(tmp_path / ".proto-dev" / "runtime" / "onlyoffice" / "workbooks"))
    monkeypatch.setattr(module, "collect_host_project_processes", lambda timeout_s: ([], None))

    state = module.collect_machine_environments(
        tmp_path,
        {"env": {}},
        [],
        {},
        {},
        {"distros": []},
        [],
        timeout_s=0.1,
    )

    roots = {row["root"]: row for row in state["roots"]}
    assert str(tmp_path) in roots
    assert not any(".proto-dev" in row["root"] for row in state["roots"])
    assert roots[str(tmp_path)]["sanity"]["status"] == "PASS"

def test_machine_environment_reports_unattributed_project_containers(monkeypatch, tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)
    monkeypatch.setattr(module, "collect_host_project_processes", lambda timeout_s: ([], None))

    docker_rows = [
        {
            "name": "main-computer-local-platform-orphan-dev-1",
            "image": "main-computer-site-orphan:latest",
            "running": True,
            "status": "running",
            "labels": {"com.docker.compose.project": "main-computer-local-platform"},
            "host_ports": [{"host_ip": "127.0.0.1", "host_port": 18101, "container_port": "8080/tcp"}],
            "project_classification": {"is_project": True, "relation": "project"},
        }
    ]
    findings: list = []

    state = module.collect_machine_environments(
        tmp_path,
        {"env": {}},
        docker_rows,
        {},
        {},
        {"distros": []},
        findings,
        timeout_s=0.1,
    )

    roots = {row["root"]: row for row in state["roots"]}
    unknown = roots[module.UNKNOWN_PROJECT_ROOT]
    assert unknown["relation"] == module.UNATTRIBUTED_PROJECT_RELATION
    assert unknown["running_deploy_count"] == 1
    assert unknown["sanity"]["status"] == "WARN"
    assert "project-looking Docker container(s) are missing path/root attribution labels" in unknown["sanity"]["issues"]
    assert state["unattributed_project_container_count"] == 1

    finding = next(
        f for f in findings
        if f.message == "Project-looking Docker container(s) could not be attributed to a checkout/root."
    )
    assert finding.evidence["container_count"] == 1
    assert finding.evidence["examples"][0]["name"] == "main-computer-local-platform-orphan-dev-1"
    assert finding.evidence["examples"][0]["ports"] == [18101]


def test_machine_environment_marks_coolify_service_paths_as_docker_internal(monkeypatch, tmp_path: Path) -> None:
    module = load_tool()
    minimal_repo(tmp_path)
    monkeypatch.setattr(module, "collect_host_project_processes", lambda timeout_s: ([], None))

    service_root = "/data/coolify/services/a5p87jbwec2sbymmx5slshjj"
    docker_rows = [
        {
            "name": "mc-coolify-site-1",
            "image": "ghcr.io/coollabsio/coolify:latest",
            "running": True,
            "status": "running",
            "labels": {
                "com.docker.compose.project": "a5p87jbwec2sbymmx5slshjj",
                "com.docker.compose.project.working_dir": service_root,
                "com.docker.compose.service": "coolify",
            },
            "host_ports": [{"host_ip": "127.0.0.1", "host_port": 3000, "container_port": "8080/tcp"}],
            "project_classification": {"is_project": True, "relation": "project"},
        }
    ]

    state = module.collect_machine_environments(
        tmp_path,
        {"env": {}},
        docker_rows,
        {},
        {},
        {"distros": []},
        [],
        timeout_s=0.1,
    )

    roots = {row["root"]: row for row in state["roots"]}
    coolify = roots[service_root]
    assert coolify["relation"] == "docker-internal-root"
    assert coolify["static_state"]["host_visibility"] == "docker-internal-linux-path"
    assert (
        "active deploy/process uses a Docker-internal Linux path that the host cannot inspect directly"
        in coolify["sanity"]["issues"]
    )

