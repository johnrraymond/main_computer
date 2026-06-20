from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from main_computer.conductor import ConductorService, discover_conductor_scripts, load_state


def test_conductor_dns_plan_uses_subprocess_without_mutating_desired_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)

        result = service.submit(
            action="dns.record.plan",
            payload={
                "zone": "example.test",
                "record_name": "worker",
                "record_type": "A",
                "record_value": "127.0.0.1",
                "ttl": 300,
                "provider_mode": "self-hosted",
            },
            confirm=False,
        )

        assert result["ok"] is True
        assert result["worker"]["result"]["applied"] is False
        assert result["worker"]["result"]["record"]["fqdn"] == "worker.example.test"
        state = load_state(service.paths.state_path)
        assert state["dns_records"] == []
        assert any(job["action"] == "dns.record.plan" and job["status"] == "planned" for job in state["jobs"].values())


def test_conductor_dns_upsert_records_revisioned_desired_state() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)
        payload = {
            "zone": "example.test",
            "record_name": "worker",
            "record_type": "A",
            "record_value": "127.0.0.1",
            "ttl": 300,
            "provider_mode": "self-hosted",
        }

        first = service.submit(action="dns.record.upsert", payload=payload, confirm=True)
        second_payload = {**payload, "record_value": "127.0.0.2"}
        second = service.submit(action="dns.record.upsert", payload=second_payload, confirm=True)

        assert first["ok"] is True
        assert second["ok"] is True
        state = load_state(service.paths.state_path)
        assert len(state["dns_records"]) == 1
        record = state["dns_records"][0]
        assert record["fqdn"] == "worker.example.test"
        assert record["record_value"] == "127.0.0.2"
        assert record["revision"] == 2


def test_conductor_generates_secret_in_private_runtime_and_public_fingerprint_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)

        result = service.submit(
            action="local.secret.generate",
            payload={"name": "worker-session", "purpose": "test conductor worker key", "bytes": 24},
            confirm=True,
        )

        assert result["ok"] is True
        state = load_state(service.paths.state_path)
        assert state["generated_keys"][0]["name"] == "worker-session"
        assert state["generated_keys"][0]["fingerprint"].startswith("sha256:")
        public_state_text = service.paths.state_path.read_text(encoding="utf-8")
        assert "secret_hex" not in public_state_text
        private_path = Path(state["generated_keys"][0]["private_path"])
        assert private_path.exists()
        secret_doc = json.loads(private_path.read_text(encoding="utf-8"))
        assert secret_doc["secret_hex"]
        assert secret_doc["fingerprint"] == state["generated_keys"][0]["fingerprint"]


def test_conductor_schedules_and_runs_due_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)
        run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        scheduled = service.submit(
            action="dns.record.upsert",
            payload={
                "zone": "example.test",
                "record_name": "api",
                "record_type": "A",
                "record_value": "127.0.0.1",
                "ttl": 300,
                "provider_mode": "self-hosted",
            },
            run_at=run_at,
            confirm=True,
            note="rotate test DNS",
        )
        assert scheduled["scheduled"] is True
        assert scheduled["job"]["status"] == "scheduled"

        due = service.run_due(now=(datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat())

        assert due["ok"] is True
        assert due["ran"] == 1
        state = load_state(service.paths.state_path)
        assert state["dns_records"][0]["fqdn"] == "api.example.test"
        assert state["jobs"][scheduled["job"]["id"]]["status"] == "completed"


def test_future_schedule_requires_explicit_confirm() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)
        run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        try:
            service.submit(
                action="local.secret.generate",
                payload={"name": "future-secret"},
                run_at=run_at,
                confirm=False,
            )
        except Exception as exc:
            assert "confirm=true" in str(exc)
        else:
            raise AssertionError("scheduled side effects must require confirm=true")



def test_conductor_discovers_repo_scripts_for_catalog() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "top_helper.py").write_text("def main():\n    print('top')\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")
        package = root / "main_computer"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "scriptish.py").write_text("import argparse\n\ndef main():\n    return 0\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n", encoding="utf-8")
        (package / "library_only.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        scripts_dir = root / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "rotate.ps1").write_text("Write-Output rotate\n", encoding="utf-8")

        catalog = discover_conductor_scripts(root)
        ids = {script["id"]: script for script in catalog}

        assert "top_helper.py" in ids
        assert ids["top_helper.py"]["kind"] == "python-file"
        assert "main_computer/scriptish.py" in ids
        assert ids["main_computer/scriptish.py"]["kind"] == "python-module"
        assert "scripts/rotate.ps1" in ids
        assert "main_computer/library_only.py" not in ids


def test_conductor_discovers_documented_calling_conventions_and_areas() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package = root / "main_computer"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "cli.py").write_text(
            "import argparse\n"
            "def main():\n"
            "    return 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n",
            encoding="utf-8",
        )
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "smoke_dns_ssl.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (root / "README.md").write_text(
            "```powershell\n"
            "python .\\scripts\\smoke_dns_ssl.py --zone example.test --issue-cert\n"
            "python -m main_computer.cli hub --network dev\n"
            "```\n",
            encoding="utf-8",
        )

        catalog = discover_conductor_scripts(root)
        ids = {script["id"]: script for script in catalog}

        assert "scripts/smoke_dns_ssl.py" in ids
        smoke = ids["scripts/smoke_dns_ssl.py"]
        assert smoke["call_conventions"][0]["doc"] == "README.md"
        assert "--zone" in smoke["call_conventions"][0]["command"]
        assert "dns-ssl-web" in smoke["areas"]
        assert "tests-smoke" in smoke["areas"]

        cli = ids["main_computer/cli.py"]
        assert any(item["command"] == "python -m main_computer.cli hub --network dev" for item in cli["call_conventions"])
        assert "hub-chain-credits" in cli["areas"]


def test_conductor_status_exposes_area_bank() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "smoke_hub_credit.py").write_text(
            "def main():\n"
            "    print('hub credit smoke')\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        service = ConductorService(root, python_executable=sys.executable)

        status = service.status()
        areas = {area["id"]: area for area in status["script_areas"]}

        assert areas["all"]["count"] == 1
        assert areas["hub-chain-credits"]["count"] == 1
        assert areas["tests-smoke"]["count"] == 1


def test_conductor_marks_curated_quarantine_first_pass_scripts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    catalog = discover_conductor_scripts(repo_root, limit=1000)
    ids = {script["id"]: script for script in catalog}

    protected = ids["scripts/smoke_protected_mode.py"]
    assert protected["quarantine_safe"] is True
    assert "quarantine-first-pass" in protected["areas"]
    assert protected["primary_area"] == "quarantine-first-pass"
    invocation = protected["suggested_invocations"][0]
    assert "--ledger-root" in invocation["args"]
    assert "runtime/quarantine/protected-mode-ledger" in invocation["args"]
    assert "--disable-syscall-pressure" in invocation["args"]
    assert invocation["timeout_s"] == 120
    assert "scripts/smoke_protected_mode.py" in invocation["command"]

    compose = ids["tools/local-platform/generate-websites-compose.py"]
    compose_args = compose["suggested_invocations"][0]["args"]
    assert "--check" in compose_args
    assert "--no-register-missing" in compose_args


def test_conductor_script_catalog_skips_patching_reports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real_scripts = root / "scripts"
        real_scripts.mkdir()
        (real_scripts / "rotate.py").write_text(
            "def main():\n    print('rotate')\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        patch_report = root / "tools" / "patching" / "reports" / "new_patch_runs" / "run1" / "bundle" / "files" / "tests"
        patch_report.mkdir(parents=True)
        (patch_report / "test_patch_artifact.py").write_text(
            "def main():\n    print('artifact')\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )

        catalog = discover_conductor_scripts(root)
        ids = {script["id"] for script in catalog}

        assert "scripts/rotate.py" in ids
        assert all(not item.startswith("tools/patching/") for item in ids)
        assert all("new_patch_runs" not in item for item in ids)


def test_conductor_script_run_plans_and_executes_catalog_script() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        script = root / "hello_conductor.py"
        script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "def main():\n"
            "    Path('ran.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
            "    print('hello', ' '.join(sys.argv[1:]))\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8",
        )
        service = ConductorService(root, python_executable=sys.executable)

        planned = service.submit(
            action="script.run",
            payload={"script": "hello_conductor.py", "args": ["planned"], "timeout_s": 30},
            confirm=False,
        )
        assert planned["ok"] is True
        assert planned["worker"]["result"]["applied"] is False
        assert not (root / "ran.txt").exists()

        ran = service.submit(
            action="script.run",
            payload={"script": "hello_conductor.py", "args": ["now"], "timeout_s": 30},
            confirm=True,
        )

        assert ran["ok"] is True
        result = ran["worker"]["result"]
        assert result["returncode"] == 0
        assert "hello now" in result["stdout_preview"]
        assert (root / "ran.txt").read_text(encoding="utf-8") == "now"


def test_conductor_script_run_rejects_paths_outside_catalog() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = ConductorService(Path(tmp), python_executable=sys.executable)

        result = service.submit(
            action="script.run",
            payload={"script": "../escape.py"},
            confirm=True,
        )

        assert result["ok"] is False
        assert "repository-relative path" in result["worker"]["error"]
