from types import SimpleNamespace

import pytest

from tools import mother_delete_non_validator_primary_nodes as mod


def test_parser_requires_nodes_only_at_run_boundary():
    args = mod.build_parser().parse_args(["--network", "mainnet"])
    assert args.node is None
    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError, match="at least one --node"):
        mod.run(args)


def test_normalize_node_is_exact_dns_style():
    assert mod._normalize_node("mainneta-super1") == "mainneta-super1"
    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError):
        mod._normalize_node("../mainneta-super1")


def test_resolve_targets_requires_exactly_one_live_service_row():
    inventory = {
        "mainneta-super1": [
            {"node": "mainneta-super1", "controller_id": "coolify-a", "service_uuid": "abcdefgh", "status": "running"}
        ]
    }
    assert mod._resolve_targets(["mainneta-super1"], inventory)[0]["service_uuid"] == "abcdefgh"
    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError, match="no exact live"):
        mod._resolve_targets(["mainnetc-super2"], inventory)


def test_resolve_targets_fails_closed_on_ambiguity():
    inventory = {
        "mainnetc-super2": [
            {"node": "mainnetc-super2", "controller_id": "coolify-a", "service_uuid": "abcdefgh", "status": "running"},
            {"node": "mainnetc-super2", "controller_id": "coolify-c", "service_uuid": "ijklmnop", "status": "running"},
        ]
    }
    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError, match="ambiguous"):
        mod._resolve_targets(["mainnetc-super2"], inventory)


def test_live_delete_requires_both_ack_flags():
    args = SimpleNamespace(
        node=["mainneta-super1"],
        execute=True,
        yes_i_know_this_deletes_live_node_services=False,
    )
    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError, match="requires both"):
        mod.run(args)


def test_operation_identity_uses_remove_node_kind():
    operation = mod._operation("mainnet")
    assert operation.operation_kind == "MOTHER-OP-REMOVE-NODE"
    assert operation.network == "mainnet"


def _args(tmp_path, node):
    return SimpleNamespace(
        node=[node],
        execute=True,
        yes_i_know_this_deletes_live_node_services=True,
        runtime_state_root=str(tmp_path / "runtime" / "state"),
        network="mainnet",
        rpc_url=None,
        timeout=30.0,
        max_wait_seconds=60.0,
        poll_interval_seconds=0.0,
        max_response_bytes=4194304,
    )


def test_run_refuses_requested_node_that_is_live_validator(tmp_path, monkeypatch):
    args = _args(tmp_path, "mainneta-super2")
    monkeypatch.setattr(mod, "read_private_state", lambda *a, **k: object())
    monkeypatch.setattr(mod, "network_document", lambda *a, **k: {"chain_id": 42424240})
    monkeypatch.setattr(mod, "_shared_rpc_route_url", lambda doc: "https://rpc.example")
    monkeypatch.setattr(
        mod,
        "_rpc",
        lambda _url, method, _params, **kwargs: (
            "0x28757b0" if method == "eth_chainId" else ["0x72151668fe7a691eab99c4779d406380c1d0cfd0"]
        ),
    )
    monkeypatch.setattr(
        mod,
        "_validator_node_map",
        lambda *a, **k: {"0x72151668fe7a691eab99c4779d406380c1d0cfd0": "mainneta-super2"},
    )
    called = {"inventory": False, "delete": False}
    monkeypatch.setattr(mod, "_service_inventory", lambda *a, **k: called.__setitem__("inventory", True))
    monkeypatch.setattr(mod, "execute_node_removal", lambda *a, **k: called.__setitem__("delete", True))

    with pytest.raises(mod.DeleteNonValidatorPrimaryNodesError, match="refusing to delete live QBFT validator"):
        mod.run(args)
    assert called == {"inventory": False, "delete": False}


def test_run_deletes_only_preflighted_non_validator_exact_service(tmp_path, monkeypatch):
    args = _args(tmp_path, "mainneta-super1")
    private_state = object()
    monkeypatch.setattr(mod, "read_private_state", lambda *a, **k: private_state)
    monkeypatch.setattr(mod, "network_document", lambda *a, **k: {"chain_id": 42424240})
    monkeypatch.setattr(mod, "_shared_rpc_route_url", lambda doc: "https://rpc.example")
    monkeypatch.setattr(
        mod,
        "_rpc",
        lambda _url, method, _params, **kwargs: (
            "0x28757b0" if method == "eth_chainId" else ["0x72151668fe7a691eab99c4779d406380c1d0cfd0"]
        ),
    )
    monkeypatch.setattr(
        mod,
        "_validator_node_map",
        lambda *a, **k: {"0x72151668fe7a691eab99c4779d406380c1d0cfd0": "mainneta-super2"},
    )
    monkeypatch.setattr(
        mod,
        "_service_inventory",
        lambda *a, **k: {
            "mainneta-super1": [
                {"node": "mainneta-super1", "controller_id": "coolify-a", "service_uuid": "snqrl1azzffhi2uqsnpvbwdd", "status": "running"}
            ]
        },
    )
    captured = {}
    def fake_delete(state, **kwargs):
        captured["state"] = state
        captured.update(kwargs)
        return {"status": "pass", "mutation_count": 1, "live_mutation_performed": True}
    monkeypatch.setattr(mod, "execute_node_removal", fake_delete)

    result = mod.run(args)
    assert result["status"] == "pass"
    assert result["mutation_count"] == 1
    assert captured["state"] is private_state
    assert captured["node"] == "mainneta-super1"
    assert captured["controller_id"] == "coolify-a"
    assert captured["service_uuid"] == "snqrl1azzffhi2uqsnpvbwdd"
    assert captured["acknowledged_node_removal"] == "REMOVE:mainneta-super1:snqrl1azzffhi2uqsnpvbwdd"
