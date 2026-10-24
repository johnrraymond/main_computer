"""Canonical Mother RPC writer embeds V2 without changing its deployment mechanics."""
from __future__ import annotations

import ast
import base64

import pytest

from tools.mother.common import deployment_validator_rpc_canary_funding as writer
from tools.mother_rpc_browser_compat_v2 import build_v2_writer_script, render_canonical_v2


@pytest.mark.parametrize("network,controller,node,chain", [
    ("mainnet", "coolify-a", "mainneta-super1", 42424240),
    ("testnet", "coolify-b", "testnetb-super1", 42424241),
])
def test_existing_writer_embeds_v2_and_manual_v2_still_works(network, controller, node, chain):
    target = {
        "network": network, "controller_id": controller,
        "route_host": f"{network}-rpc.greatlibrary.io",
        "target_host": node, "target_port": 8545,
        "target_url": f"http://{node}:8545",
        "expected_chain_id": chain,
        "dynamic_file": f"mother-{network}-rpc-route-{controller}.yml",
        "legacy_dynamic_file": None if network == "testnet" else "main-computer-mainnet-rpc-public-entry-a.yml",
    }
    script = writer._route_writer_script(target)
    assignments = [n.value for n in ast.parse(script).body
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "DYNAMIC_CONFIG_B64" for t in n.targets)]
    assert len(assignments) == 1
    assert base64.b64decode(ast.literal_eval(assignments[0])).decode() == render_canonical_v2(target)
    assert "EXPECTED_CHAIN_ID_HEX = " + repr(hex(chain)) in script
    assert "docker_exec(proxy_id, script)" in script
    assert "exit 65" in build_v2_writer_script(target)
    assert "exit 65" in build_v2_writer_script(target, rollback=True)


def test_implicit_mainnet_uses_same_v2_route():
    target = {
        "controller_id": "coolify-a", "route_host": "mainnet-rpc.greatlibrary.io",
        "target_host": "mainneta-super1", "target_port": 8545,
        "target_url": "http://mainneta-super1:8545", "expected_chain_id": 42424240,
        "dynamic_file": "mother-mainnet-rpc-route-coolify-a.yml",
        "legacy_dynamic_file": "main-computer-mainnet-rpc-public-entry-a.yml",
    }
    assert writer._route_writer_script(target) == writer._route_writer_script({**target, "network": "mainnet"})
