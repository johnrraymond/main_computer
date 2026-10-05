from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import tools.mother.preflight_paranoia_snap as snap


A1 = "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6"
C3 = "0x5437faf49eb90f64d3dc85ef308ebc5555df1b14"
A2 = "0x72151668fe7a691eab99c4779d406380c1d0cfd0"


def _compose(node: str, mode: str, min_peers: int) -> str:
    return f"""
services:
  {node}:
    image: hyperledger/besu:26.9.0
    command:
      - --network-id=42424240
      - --sync-mode={mode}
      - --sync-min-peers={min_peers}
"""


def _install(
    monkeypatch,
    tmp_path: Path,
    specs: list[tuple[str, str, str, str, int, str]],
) -> Path:
    # node, validator, controller, mode, min_peers, status
    topology_path = tmp_path / "topology.json"
    topology_path.write_text("{}", encoding="utf-8")
    nodes = [item[0] for item in specs]
    validators = [item[1] for item in specs]
    services = [
        {
            "node": node,
            "controller_id": controller,
            "service_uuid": f"uuid-{node}",
        }
        for node, _validator, controller, _mode, _min_peers, _status in specs
    ]
    record = {
        "path": topology_path,
        "sha256": "a" * 64,
        "nodes": nodes,
        "services": services,
        "document": {"network": "mainnet"},
        "topology": {
            "nodes": nodes,
            "validator_set": validators,
            "validator_count": len(validators),
        },
    }
    by_uuid = {
        f"uuid-{node}": {
            "node": node,
            "mode": mode,
            "min_peers": min_peers,
            "status": status,
        }
        for node, _validator, _controller, mode, min_peers, status in specs
    }

    monkeypatch.setattr(snap, "_discover_current_topology_evidence", lambda *args, **kwargs: topology_path)
    monkeypatch.setattr(snap, "_load_private_state", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        snap,
        "_load_acknowledged_current_topology_for_preflight",
        lambda *args, **kwargs: record,
    )
    monkeypatch.setattr(snap, "_controller", lambda *args, **kwargs: object())

    def detail(_controller, service_uuid, **kwargs):  # noqa: ARG001
        item = by_uuid[service_uuid]
        return {
            "missing": False,
            "payload": {
                "status": item["status"],
                "docker_compose_raw": _compose(item["node"], item["mode"], item["min_peers"]),
            },
        }

    monkeypatch.setattr(snap, "_service_detail", detail)
    return topology_path


def test_two_full_healthy_validators_pass(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "FULL", 0, "running:healthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["status"] == "pass"
    assert result["safe_to_add_snap"] is True
    assert result["blockers"] == []
    assert result["summary"]["two_validator_full_foundation_required"] is True
    assert result["summary"]["network_mutation_performed"] is False


def test_two_validator_foundation_blocks_snap_incumbent_and_emits_manual_full_command(
    monkeypatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "SNAP", 2, "running:healthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["status"] == "blocked"
    assert result["safe_to_add_snap"] is False
    assert any(item["code"] == "two-validator-foundation-not-full" for item in result["blockers"])
    assert len(result["manual_fix_commands"]) == 1
    command = result["manual_fix_commands"][0]
    assert "mainnetc-super3" in command
    assert "--service-uuid uuid-mainnetc-super3" in command
    assert "--sync-mode FULL" in command


def test_three_validator_valid_mixed_profiles_pass(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "FULL", 0, "running:healthy"),
            ("mainneta-super2", A2, "coolify-a", "SNAP", 2, "running:healthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["safe_to_add_snap"] is True
    assert result["blockers"] == []
    assert result["warnings"][0]["code"] == "mixed-profile-foundation-observed"


def test_snap_zero_profile_is_blocked_and_repair_command_preserves_snap(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "FULL", 0, "running:healthy"),
            ("mainneta-super2", A2, "coolify-a", "SNAP", 0, "running:healthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["safe_to_add_snap"] is False
    assert any(item["code"] == "invalid-sync-profile-pair" for item in result["blockers"])
    assert len(result["manual_fix_commands"]) == 1
    assert "--sync-mode SNAP" in result["manual_fix_commands"][0]


def test_unhealthy_incumbent_blocks_snap_add(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "FULL", 0, "starting:unhealthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["safe_to_add_snap"] is False
    assert any(item["code"] == "incumbent-not-running-healthy" for item in result["blockers"])


def test_one_validator_blocks_snap_and_says_use_full(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy")],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["safe_to_add_snap"] is False
    blocker = next(item for item in result["blockers"] if item["code"] == "insufficient-snap-foundation")
    assert "FULL, not SNAP" in blocker["message"]
    assert result["manual_fix_commands"] == []


def test_auto_discovered_relative_path_is_canonicalized_before_acknowledged_load(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    relative_root = Path("runtime/state")
    discovered = relative_root / "mother/evidence/deployment-node-add-post-admission-observe/topology.json"
    discovered.parent.mkdir(parents=True, exist_ok=True)
    discovered.write_text("{}", encoding="utf-8")

    captured: dict[str, object] = {}
    record = {
        "path": discovered.resolve(),
        "sha256": "a" * 64,
        "nodes": [],
        "services": [],
        "document": {"network": "mainnet"},
        "topology": {"nodes": [], "validator_set": [], "validator_count": 0},
    }

    monkeypatch.setattr(
        snap,
        "_discover_current_topology_evidence",
        lambda *args, **kwargs: discovered,
    )
    monkeypatch.setattr(snap, "_load_private_state", lambda *args, **kwargs: SimpleNamespace())

    def load_ack(*args, **kwargs):
        captured["topology_evidence"] = kwargs["topology_evidence"]
        return record

    monkeypatch.setattr(snap, "_load_acknowledged_current_topology_for_preflight", load_ack)

    result = snap.run_snap_preflight(runtime_state_root=relative_root, network="mainnet")

    selected = captured["topology_evidence"]
    assert isinstance(selected, Path)
    assert selected.is_absolute()
    assert selected == discovered.resolve()
    assert "runtime/state/mother/runtime/state/mother" not in selected.as_posix()
    assert result["status"] == "blocked"



def test_two_validator_snap_zero_emits_only_full_remediation(monkeypatch, tmp_path: Path) -> None:
    _install(
        monkeypatch,
        tmp_path,
        [
            ("mainneta-super1", A1, "coolify-a", "FULL", 0, "running:healthy"),
            ("mainnetc-super3", C3, "coolify-c", "SNAP", 0, "running:healthy"),
        ],
    )

    result = snap.run_snap_preflight(runtime_state_root=tmp_path, network="mainnet")

    assert result["safe_to_add_snap"] is False
    assert len(result["manual_fix_commands"]) == 1
    command = result["manual_fix_commands"][0]
    assert "mainnetc-super3" in command
    assert "--sync-mode FULL" in command
    assert "--sync-mode SNAP" not in command
    assert result["summary"]["manual_remediation_command_count"] == 1


def test_cli_prints_manual_remediation_and_exits_nonzero(monkeypatch, capsys) -> None:
    command = (
        "python .\\tools\\mother\\node_sync_mode_switch_smoke.py mainnetc-super3 `\n"
        "  --network mainnet `\n"
        "  --service-uuid uuid-mainnetc-super3 `\n"
        "  --sync-mode FULL `\n"
        "  --execute-mutations"
    )
    monkeypatch.setattr(
        snap,
        "run_snap_preflight",
        lambda **kwargs: {
            "safe_to_add_snap": False,
            "manual_fix_commands": [command],
            "blockers": [{"code": "two-validator-foundation-not-full"}],
            "status": "blocked",
        },
    )

    rc = snap.main(["--network", "mainnet"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "MOTHER_PREFLIGHT_PARANOIA_SNAP_REMEDIATION_REQUIRED" in out
    assert command in out
