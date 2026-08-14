from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from tools.mother.common.canonical import canonical_json, canonical_yaml


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


def test_reseal_repairs_explicitly_edited_identity_without_changing_generation(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    installed = _run(
        "bootstrap", "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test", "--write",
    )
    assert installed.returncode == 0, installed.stderr

    root = runtime / "mother"
    identity = root / "identity.private.yaml"
    document = _document()
    replacement_token = "1|REPLACEMENTSECRETTOKENVALUE654321"
    document["networks"]["testnet"]["coolify"]["controllers"]["coolify-b"]["api_token"] = replacement_token  # type: ignore[index]
    # Deliberately write non-canonical YAML to prove reseal normalizes the local edit.
    import yaml
    identity.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    rejected = _run("validate", "--runtime-state-root", str(runtime))
    assert rejected.returncode != 0
    assert "MOTHER_STATE_" in rejected.stderr

    dry_run = _run(
        "reseal",
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-08-14T20:00:00Z",
        "--operation-id", "manual-token-reseal-test",
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "recovery manifest/reference verification: passed" in dry_run.stdout
    assert "private-state reseal required: yes" in dry_run.stdout
    assert "write performed: no (dry-run)" in dry_run.stdout
    assert replacement_token not in dry_run.stdout
    assert replacement_token not in dry_run.stderr

    written = _run(
        "reseal",
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-08-14T20:00:00Z",
        "--operation-id", "manual-token-reseal-test",
        "--write",
    )
    assert written.returncode == 0, written.stderr
    assert "private-state reseal required: yes" in written.stdout
    assert "generation preserved: 1" in written.stdout
    assert "write performed: yes" in written.stdout
    assert "stable read: passed" in written.stdout
    assert replacement_token not in written.stdout
    assert identity.read_bytes() == canonical_yaml(document)

    validated = _run("validate", "--runtime-state-root", str(runtime))
    assert validated.returncode == 0, validated.stderr
    assert "private-state generation: 1" in validated.stdout

    second = _run("reseal", "--runtime-state-root", str(runtime), "--write")
    assert second.returncode == 0, second.stderr
    assert "private-state reseal required: no" in second.stdout
    assert "write performed: no (already sealed)" in second.stdout


def test_reseal_refuses_recovery_manifest_mismatch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runtime = tmp_path / "runtime" / "state"
    installed = _run(
        "bootstrap", "--source", str(source),
        "--runtime-state-root", str(runtime),
        "--updated-at", "2026-07-30T22:10:00Z",
        "--operation-id", "mother-state-bootstrap-test", "--write",
    )
    assert installed.returncode == 0, installed.stderr

    root = runtime / "mother"
    identity = root / "identity.private.yaml"
    document = _document()
    document["networks"]["testnet"]["coolify"]["controllers"]["coolify-b"]["api_token"] = "1|REPLACEMENTSECRETTOKENVALUE654321"  # type: ignore[index]
    identity.write_bytes(canonical_yaml(document))

    manifest_path = root / "private-recovery" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["private_state_generation"] = 2
    manifest_path.write_bytes(canonical_json(manifest))

    refused = _run(
        "reseal",
        "--runtime-state-root", str(runtime),
        "--operation-id", "manual-token-reseal-test",
        "--write",
    )
    assert refused.returncode != 0
    assert "MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH" in refused.stderr
