from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.hub_control import remove_hub
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import read_accepted, require_operation


ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture(autouse=True)
def _legacy_removal_fixture(monkeypatch):
    # Topology contract tests do not bootstrap verified Mother private state.
    # The actual wallet release CAS is independently tested in wallet lifecycle tests.
    monkeypatch.setattr(remove_hub, "transition_hub_identity", lambda *a, **kw: {"status": "inactive"})



def _ctx(tmp_path: Path) -> HubContext:
    return HubContext(
        repo_root=ROOT,
        hub_state_root=tmp_path / "runtime" / "state" / "hub",
        fdb_state_root=tmp_path / "runtime" / "state" / "fdb",
        chain_state_root=tmp_path / "runtime" / "state" / "chain",
        mother_private_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.yaml",
        mother_metadata_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.meta.json",
    )


def _seed_private(ctx: HubContext) -> None:
    ctx.mother_private_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.mother_private_path.write_text(
        """
networks:
  mainnet:
    coolify:
      controllers:
        coolify-a:
          url: http://coolify-a.invalid
          api_token: token-a
          project_uuid: project-a
          server_uuid: server-a
        coolify-c:
          url: http://coolify-c.invalid
          api_token: token-c
          project_uuid: project-c
          server_uuid: server-c
""".strip() + "\n",
        encoding="utf-8",
    )
    ctx.mother_metadata_path.write_text(json.dumps({"generation": 12}), encoding="utf-8")


def _hub(hub_id: str, controller: str, url: str, *, chain_generation: int = 5) -> dict:
    return {
        "hub_id": hub_id,
        "controller_id": controller,
        "host_id": controller,
        "public_url": url,
        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
        "chain_contract": {"generation": chain_generation, "sha256": f"chain{chain_generation}"},
    }


def _seed_accepted(ctx: HubContext, hubs: list[dict], *, generation: int = 1) -> dict:
    payload = {
        "schema": "main-computer.hub-accepted.v1",
        "network": "mainnet",
        "generation": generation,
        "cluster_id": "main-computer-mainnet-hubs",
        "hubs": hubs,
        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
        "chain_contract": {"generation": 5, "sha256": "chain5"},
    }
    path = ctx.hub_state_root / "mainnet" / "accepted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _absent(_target):
    return {
        "verified": True,
        "verified_absent": True,
        "reason": "hub-deployment-absent",
        "deployment_deleted": True,
        "action": "deleted",
        "application_uuid": "app-1",
    }


def test_remove_final_hub_requires_explicit_full_deletion_ack(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_private(ctx)
    _seed_accepted(ctx, [_hub("mainneta-hub1", "coolify-a", "https://mainnet-hub.example.invalid")])

    with pytest.raises(HubControlError) as exc_info:
        remove_hub.prep(
            ctx,
            "mainnet",
            "mainneta-hub1",
            deployment_inspector=lambda _target: {"present": True, "application_uuid": "app-1"},
        )

    assert exc_info.value.code == "HUB_FULL_DELETION_REQUIRES_ACK"


def test_remove_final_hub_round_trip_accepts_empty_generation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_private(ctx)
    original = _seed_accepted(
        ctx,
        [_hub("mainneta-hub1", "coolify-a", "https://mainnet-hub.example.invalid")],
        generation=1,
    )

    prepared = remove_hub.prep(
        ctx,
        "mainnet",
        "mainneta-hub1",
        allow_full_deletion=True,
        deployment_inspector=lambda _target: {
            "present": True,
            "application_uuid": "app-1",
            "environment_name": "mainnet-hubs",
        },
    )
    operation_id = prepared["details"]["operation_id"]
    assert prepared["details"]["full_deletion"] is True
    assert prepared["details"]["target_hub_count"] == 0
    op = require_operation(ctx, "mainnet", operation_id)
    assert op["target"]["application_uuid"] == "app-1"

    removed = remove_hub.do(ctx, "mainnet", operation_id, remover=_absent)
    assert removed["status"] == "removed"
    assert removed["details"]["deployment_deleted"] is True

    proof = remove_hub.inspect_operation(ctx, "mainnet", operation_id, inspector=_absent)
    assert proof["hub_remove_verification"]["verified"] is True

    finalized = remove_hub.finalize(ctx, "mainnet", operation_id, inspector=_absent)
    assert finalized["status"] == "finalized"
    assert finalized["details"]["accepted_generation"] == 2
    assert finalized["details"]["full_deletion"] is True

    accepted = read_accepted(ctx, "mainnet")
    assert accepted is not None
    assert accepted["generation"] == 2
    assert accepted["hubs"] == []
    assert accepted["fdb_contract"] == original["fdb_contract"]
    assert accepted["chain_contract"] == original["chain_contract"]


def test_ordinary_remove_preserves_survivor_and_dependency_authority(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_private(ctx)
    original = _seed_accepted(
        ctx,
        [
            _hub("mainneta-hub1", "coolify-a", "https://a-hub.example.invalid"),
            _hub("mainnetc-hub1", "coolify-c", "https://c-hub.example.invalid"),
        ],
        generation=7,
    )

    prepared = remove_hub.prep(
        ctx,
        "mainnet",
        "mainnetc-hub1",
        deployment_inspector=lambda _target: {"present": True, "application_uuid": "app-c"},
    )
    operation_id = prepared["details"]["operation_id"]
    assert prepared["details"]["target_hub_count"] == 1
    assert prepared["details"]["full_deletion"] is False

    remove_hub.do(ctx, "mainnet", operation_id, remover=_absent)
    remove_hub.finalize(ctx, "mainnet", operation_id, inspector=_absent)

    accepted = read_accepted(ctx, "mainnet")
    assert accepted is not None
    assert accepted["generation"] == 8
    assert [item["hub_id"] for item in accepted["hubs"]] == ["mainneta-hub1"]
    assert accepted["fdb_contract"] == original["fdb_contract"]
    assert accepted["chain_contract"] == original["chain_contract"]
