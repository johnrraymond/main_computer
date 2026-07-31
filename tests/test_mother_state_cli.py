from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from tools.mother.common.canonical import canonical_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_state.py"


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "testnet": {
                "coolify": {
                    "controllers": {
                        "coolify-b": {
                            "url": "http://127.0.0.1:8000/",
                            "api_token": "1|THISISASECRETTOKENVALUE123456",
                        }
                    },
                    "mutation_authority": "observe-only",
                },
                "nodes": {},
                "validators": {},
                "wallets": {
                    "deployer": {
                        "private_key": "0x" + "11" * 32,
                    }
                },
            }
        },
    }


def _run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "mother-bootstrap.private.yaml"
    # Deliberately non-canonical input proves bootstrap canonicalizes operator YAML.
    path.write_text(
        "schema_version: 1\nkind: main_computer.mother.private_state.v1\n"
        "networks:\n  testnet:\n    wallets:\n      deployer:\n"
        f"        private_key: '0x{'11' * 32}'\n"
        "    validators: {}\n    nodes: {}\n    coolify:\n"
        "      mutation_authority: observe-only\n      controllers:\n"
        "        coolify-b:\n          url: http://127.0.0.1:8000/\n"
        "          api_token: 1|THISISASECRETTOKENVALUE123456\n",
        encoding="utf-8",
    )
    return path


def test_bootstrap_is_dry_run_by_default(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    result = _run(
        "bootstrap",
        "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test",
    )
    assert result.returncode == 0, result.stderr
    assert "write performed: no (dry-run)" in result.stdout
    assert "source canonicalization: required" in result.stdout
    assert "testnet/coolify-b" in result.stdout
    assert not (runtime / "mother").exists()


def test_bootstrap_write_installs_manifest_last_bundle_and_validate_reads_it(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    result = _run(
        "bootstrap",
        "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test",
        "--write",
    )
    assert result.returncode == 0, result.stderr
    assert "write performed: yes" in result.stdout
    assert "stable read: passed" in result.stdout

    root = runtime / "mother"
    identity = root / "identity.private.yaml"
    metadata = root / "identity.private.meta.json"
    manifest = root / "private-recovery" / "manifest.json"
    assert identity.read_bytes() == canonical_yaml(_document())
    assert metadata.is_file()
    assert manifest.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"] == []
    if os.name != "nt":
        assert identity.stat().st_mode & 0o777 == 0o600
        assert metadata.stat().st_mode & 0o777 == 0o600
        assert manifest.stat().st_mode & 0o777 == 0o600
        assert root.stat().st_mode & 0o777 == 0o700

    validated = _run(
        "validate",
        "--runtime-state-root", str(runtime),
        "--operation-id", "mother-state-validate-test",
    )
    assert validated.returncode == 0, validated.stderr
    assert "private-state generation: 1" in validated.stdout
    assert "stable read: passed" in validated.stdout
    assert "permissions/owner: verified" in validated.stdout
    assert "secrets printed: 0" in validated.stdout
    assert "THISISASECRETTOKEN" not in validated.stdout
    assert "0x" + "11" * 32 not in validated.stdout


def test_show_requires_redaction_and_never_prints_secret_material(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    installed = _run(
        "bootstrap", "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test", "--write",
    )
    assert installed.returncode == 0, installed.stderr

    rejected = _run("show", "--runtime-state-root", str(runtime))
    assert rejected.returncode == 2
    assert "show requires --redacted" in rejected.stderr

    shown = _run("show", "--runtime-state-root", str(runtime), "--redacted")
    assert shown.returncode == 0, shown.stderr
    assert shown.stderr == ""
    assert shown.stdout.count('"<redacted>"') == 2
    assert "THISISASECRETTOKEN" not in shown.stdout
    assert "0x" + "11" * 32 not in shown.stdout
    assert "private_key_path" not in shown.stdout
    assert "testnet" in shown.stdout


def test_bootstrap_is_idempotent_for_same_document_and_rejects_different_state(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    first = _run(
        "bootstrap", "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test", "--write",
    )
    assert first.returncode == 0, first.stderr

    same = _run(
        "bootstrap", "--source", str(source),
        "--runtime-state-root", str(runtime), "--write",
    )
    assert same.returncode == 0, same.stderr
    assert "target: already-committed" in same.stdout
    assert "write performed: no" in same.stdout

    different = _document()
    different["networks"]["testnet"]["coolify"]["controllers"]["coolify-b"]["url"] = "http://127.0.0.1:9000/"  # type: ignore[index]
    other = tmp_path / "different.private.yaml"
    other.write_bytes(canonical_yaml(different))
    conflict = _run(
        "bootstrap", "--source", str(other),
        "--runtime-state-root", str(runtime), "--write",
    )
    assert conflict.returncode == 2
    assert "different committed Mother private state" in conflict.stderr


def test_gitignore_excludes_complete_mother_tree_and_bootstrap_sources() -> None:
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/state/mother/" in text
    assert "runtime/state/*.private.yaml" in text
