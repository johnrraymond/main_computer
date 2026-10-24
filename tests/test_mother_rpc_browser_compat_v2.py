"""Canonical Mother RPC browser policy, guarded updates, and route preservation."""
from __future__ import annotations

import ast
import base64
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from tools.mother.common import deployment_validator_rpc_canary_funding as writer
from tools import mother_rpc_browser_compat_v2 as v2


@lru_cache(maxsize=1)
def _working_bash() -> str | None:
    """Find an executable POSIX Bash, not an unusable Windows WSL launcher.

    On Windows, `bash.exe` may be the WSL launcher even when no Linux
    distribution has /bin/bash. Prefer Git for Windows' Bash if present and
    *execute* a small command before accepting any candidate.
    """
    candidates: list[str] = []
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base:
                candidates.extend((
                    str(Path(base) / "Git" / "bin" / "bash.exe"),
                    str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
                ))
    on_path = shutil.which("bash")
    if on_path:
        candidates.append(on_path)

    seen: set[str] = set()
    for executable in candidates:
        normalized = os.path.normcase(os.path.abspath(executable))
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            result = subprocess.run(
                [executable, "-c", "printf 'MOTHER_V2_BASH_READY'"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout == "MOTHER_V2_BASH_READY":
            return executable
    return None


def _require_working_bash() -> str:
    executable = _working_bash()
    if executable is None:
        pytest.skip(
            "Bash-only helper runtime test: no functioning Bash found. "
            "Windows WSL's bash launcher is not sufficient without /bin/bash; "
            "use Git Bash or run these tests on Linux."
        )
    return executable


@pytest.fixture(params=[("testnet", "coolify-b", "testnetb-super1", 42424241),
                        ("mainnet", "coolify-a", "mainneta-super1", 42424240)])
def target(request):
    network, controller, node, chain = request.param
    return {"network": network, "controller_id": controller,
            "route_host": f"{network}-rpc.greatlibrary.io",
            "target_host": node, "target_url": f"http://{node}:8545",
            "target_port": 8545, "expected_chain_id": chain,
            "dynamic_file": f"mother-{network}-rpc-route-{controller}.yml",
            "legacy_dynamic_file": None if network == "testnet" else
            "main-computer-mainnet-rpc-public-entry-a.yml"}


def test_v2_is_additive_and_keeps_legacy_generator_unchanged(target):
    before = writer._render_shared_rpc_traefik_dynamic_config(target)
    after = v2.render_v2(target)
    assert writer._render_shared_rpc_traefik_dynamic_config(target) == before
    legacy = yaml.safe_load(before)["http"]
    updated = yaml.safe_load(after)["http"]
    assert "middlewares" not in legacy
    assert updated["services"] == legacy["services"]
    assert len(updated["routers"]) == 8   # unchanged 2 fallback + 6 exact-Origin routes
    assert len(updated["middlewares"]) == 3
    assert "*" not in v2.ORIGINS
    for name, original in legacy["routers"].items():
        assert updated["routers"][name] == original
        for suffix, origin in zip(("worker-ip", "worker-localhost", "metamask"), v2.ORIGINS):
            router = updated["routers"][f"{name}-{suffix}"]
            assert router["service"] == original["service"]
            assert router["entryPoints"] == original["entryPoints"]
            assert router.get("tls") == original.get("tls")
            assert router["priority"] > len(original["rule"])
            assert router["rule"] == f"Host(`{target['route_host']}`) && Header(`Origin`, `{origin}`)"
            assert len(router["middlewares"]) == 1
            mid = router["middlewares"][0]
            headers = updated["middlewares"][mid]["headers"]
            assert headers["accessControlAllowOriginList"] == [origin]
            assert headers["accessControlAllowMethods"] == ["POST", "OPTIONS"]
            assert headers["customRequestHeaders"] == {"Origin": ""}
            assert headers["customResponseHeaders"] == {"Access-Control-Allow-Origin": origin}
            assert headers["addVaryHeader"] is True
    assert len(set(updated["middlewares"])) == 3


def test_post_cors_response_is_explicit_even_after_origin_removed(target):
    """Regression for OPTIONS 200 / POST 200 but browser-CORS rejection.

    Traefik v3.3's headers middleware reads response Request.Header Origin
    after customRequestHeaders has removed it. Therefore access-control origin
    MUST be explicitly set in customResponseHeaders on an exact-Origin router.
    """
    dynamic = yaml.safe_load(v2.render_v2(target))["http"]
    for origin in v2.ORIGINS:
        names = [name for name, router in dynamic["routers"].items()
                 if router["rule"].endswith(f"Header(`Origin`, `{origin}`)")]
        assert len(names) == 2   # one on HTTP and one on HTTPS
        for name in names:
            headers = dynamic["middlewares"][dynamic["routers"][name]["middlewares"][0]]["headers"]
            # The last custom response setting survives after req.Origin is stripped.
            assert headers["customResponseHeaders"].get("Access-Control-Allow-Origin") == origin
            assert headers["customRequestHeaders"].get("Origin") == ""
    # Requests without an approved Origin never receive a fabricated CORS response.
    for name, router in dynamic["routers"].items():
        if "Header(`Origin`" not in router["rule"]:
            assert "middlewares" not in router


def test_guard_recognizes_initial_v2_install_byte_for_byte(target):
    # Upgrading today's deployed opt-in V2 must be permitted without weakening
    # the byte-exact guard to accept unknown file contents.
    original = v2.render_initial_v2(target)
    encoded = base64.b64encode(original.encode("utf-8")).decode("ascii")
    helper = v2.build_v2_writer_script(target)
    assert encoded in helper
    assert "cmp -s" in helper
    assert original != v2.render_v2(target)


def test_writer_includes_guard_rollback_and_preserves_legacy_writer(target):
    legacy = writer._route_writer_script(target)
    proposed = v2.build_v2_writer_script(target)
    assert writer._route_writer_script(target) == legacy
    assert "exit 65" in proposed and "cmp -s" in proposed
    assert "v2_backup=" in proposed
    assert "Access-Control-Allow-Origin:" in proposed
    assert "wget -S -T 8 -O-" in proposed
    for origin in v2.ORIGINS:
        assert origin in proposed
    assert "trap - EXIT" in proposed
    assert "cp \"$v2_backup\"" in proposed
    assert "DYNAMIC_CONFIG_B64" in proposed
    assert "test -f" in proposed
    if target["legacy_dynamic_file"]:
        assert "test ! -e" in proposed
        assert "rm -f '{LEGACY_TARGET_PATH}'" not in proposed


def test_v2_writer_rejects_modified_legacy_template(target):
    with pytest.raises(v2.RpcBrowserCompatV2Error, match="changed"):
        v2.build_v2_writer_script(target, legacy_script=lambda _target: "unrecognized")


def test_live_mutation_scoped_hook_restored(monkeypatch, target):
    from tools import mother_rpc_route_ensure as route
    original = writer._route_writer_script
    called = []

    def fake_ensure(_state, **kwargs):
        called.append((kwargs, writer._route_writer_script(target)))
        return {"status": "pass", "proof": {"healthy": True}, "cleanup": {"deleted": True}}

    monkeypatch.setattr(route, "ensure_route", fake_ensure)
    result = v2.execute_v2(SimpleNamespace(), network=target["network"], node=target["target_host"],
                           controller_id=target["controller_id"], timeout=5, max_wait_seconds=30)
    assert result["mode"] == "rpc-browser-compat-v2-opt-in"
    assert called and "exit 65" in called[0][1]
    assert writer._route_writer_script is original


def test_scoped_hook_restored_after_error(monkeypatch, target):
    from tools import mother_rpc_route_ensure as route
    original = writer._route_writer_script

    def fail(*args, **kwargs):
        assert writer._route_writer_script is not original
        raise RuntimeError("simulated Coolify failure")

    monkeypatch.setattr(route, "ensure_route", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        v2.execute_v2(SimpleNamespace(), network=target["network"], node=target["target_host"],
                       controller_id=target["controller_id"], timeout=5, max_wait_seconds=30)
    assert writer._route_writer_script is original


def test_harness_v2_command_is_an_explicit_early_dispatch():
    harness = (v2.ROOT / "mother_mutate_harness.py").read_text(encoding="utf-8")
    assert 'sys.argv[1] == "rpc-browser-compat-v2"' in harness
    assert 'v2_main(sys.argv[2:])' in harness
    result = subprocess.run(
        [sys.executable, str(v2.ROOT / "mother_mutate_harness.py"), "rpc-browser-compat-v2", "--help"],
        cwd=v2.ROOT, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--execute-mutations" in result.stdout


def test_harness_rejects_mutation_without_both_flags():
    base = [sys.executable, str(v2.ROOT / "mother_mutate_harness.py"), "rpc-browser-compat-v2",
            "--network", "testnet", "--node", "testnetb-super1", "--host", "coolify-b"]
    for extra in (["--execute-mutations"], ["--yes-i-know-this-mutates-target-host"]):
        p = subprocess.run(base + extra, cwd=v2.ROOT, text=True, capture_output=True, timeout=30)
        assert p.returncode != 0
        assert "requires both" in p.stderr


def test_generated_v2_helper_shell_is_syntactically_valid(target):
    generated = v2.build_v2_writer_script(target)
    tree = ast.parse(generated)
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and any(
        isinstance(k, ast.Name) and k.id == "script" for k in node.targets))
    context = {"rpc_payload": '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
               "backend_url": target["target_url"]}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(k, ast.Name) and k.id in (
            "ROUTE_HOST", "TARGET_HOST", "TARGET_PORT", "EXPECTED_CHAIN_ID_HEX", "DYNAMIC_CONFIG_B64", "TARGET_PATH", "LEGACY_TARGET_PATH")
            for k in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<v2>", "exec"), context)
    shell = eval(compile(ast.Expression(assignment.value), "<v2>", "eval"), context)
    # These structural assertions run even on Windows hosts without Bash.
    assert shell.index("cmp -s") < shell.index(".tmp")
    assert shell.index("trap 'cp") < shell.index("mv '")
    assert shell.rindex("trap - EXIT") > shell.index("route_out=")
    assert shell.index("route_out=") < shell.index("cors_out=") < shell.index("trap - EXIT")
    origins = v2.permitted_origins(v2.canonical_test_extension_id(target["network"]))
    assert shell.count("cors_out=$(wget -S") == len(origins)
    assert shell.count("grep -iqF") == len(origins)
    outcome = subprocess.run([_require_working_bash(), "-n"], input=shell, text=True, capture_output=True)
    assert outcome.returncode == 0, outcome.stderr


def test_explicit_rollback_restores_legacy_bytes_without_changing_normal_writer(target):
    baseline = writer._route_writer_script(target)
    revert = v2.build_v2_writer_script(target, rollback=True)
    expected_legacy = base64.b64encode(writer._render_shared_rpc_traefik_dynamic_config(target).encode()).decode()
    assert f"DYNAMIC_CONFIG_B64 = {expected_legacy!r}" in revert
    assert "exit 65" in revert and "cmp -s" in revert
    assert writer._route_writer_script(target) == baseline

@pytest.mark.parametrize("outcome", ["good", "no-cors", "wrong-chain"])
def test_generated_post_probe_rejects_missing_headers_or_wrong_chain(target, outcome):
    """Exercise the exact shell from the generated Coolify helper with fake HTTP IO.

    The real deployment still requires live verification. This prevents another
    OPTIONS-only success from being reported as a working Origin-bearing POST.
    """
    generated = v2.build_v2_writer_script(target)
    tree = ast.parse(generated)
    statement = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(k, ast.Name) and k.id == "script" for k in node.targets))
    context = {"rpc_payload": '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
               "backend_url": target["target_url"]}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(k, ast.Name) and k.id in (
            "ROUTE_HOST", "TARGET_HOST", "TARGET_PORT", "EXPECTED_CHAIN_ID_HEX", "DYNAMIC_CONFIG_B64",
            "TARGET_PATH", "LEGACY_TARGET_PATH") for k in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<v2>", "exec"), context)
    shell = eval(compile(ast.Expression(statement.value), "<v2>", "eval"), context)
    probe = shell[shell.index("cors_out="):shell.index("trap - EXIT")]
    # Never count the absence of Bash itself as a successful negative probe.
    # The checks below must actually execute to prove good, no-cors and
    # wrong-chain outcomes; otherwise each case is explicitly skipped.
    bash = _require_working_bash()
    # Mock wget as a Bash function: no platform-specific executable shims,
    # temp PATH handling, or Python shebang resolution under Git for Windows.
    fake_wget = r'''wget() {
      local origin='' arg value
      for arg in "$@"; do
        case "$arg" in
          '--header=Origin: '*) origin=${arg#--header=Origin: };;
        esac
      done
      printf '  HTTP/1.1 200 OK\n' >&2
      if [ "$TEST_OUTCOME" != no-cors ]; then
        printf '  Access-Control-Allow-Origin: %s\n' "$origin" >&2
      fi
      if [ "$TEST_OUTCOME" = wrong-chain ]; then value=0x0; else value=$EXPECTED_HEX; fi
      printf '{"jsonrpc":"2.0","id":1,"result":"%s"}\n' "$value"
    }
    '''
    env = dict(os.environ, TEST_OUTCOME=outcome, EXPECTED_HEX=hex(target["expected_chain_id"]))
    result = subprocess.run([bash, "-ec", "set -eu\n" + fake_wget + probe], env=env,
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == (outcome == "good"), result.stderr
    if outcome != "good":
        # A missing shell or a PATH error must never satisfy this assertion.
        assert "WSL" not in result.stderr and "not found" not in result.stderr, result.stderr



def test_generated_post_probe_contains_all_chain_and_cors_assertions(target):
    """Cross-platform structural coverage even when Bash execution is unavailable."""
    generated = v2.build_v2_writer_script(target)
    tree = ast.parse(generated)
    statement = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(k, ast.Name) and k.id == "script" for k in node.targets))
    context = {"rpc_payload": '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
               "backend_url": target["target_url"]}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(k, ast.Name) and k.id in (
            "ROUTE_HOST", "TARGET_HOST", "TARGET_PORT", "EXPECTED_CHAIN_ID_HEX", "DYNAMIC_CONFIG_B64",
            "TARGET_PATH", "LEGACY_TARGET_PATH") for k in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<v2>", "exec"), context)
    shell = eval(compile(ast.Expression(statement.value), "<v2>", "eval"), context)
    probe = shell[shell.index("cors_out="):shell.index("trap - EXIT")]
    expected_chain = hex(target["expected_chain_id"])
    origins = v2.permitted_origins(v2.canonical_test_extension_id(target["network"]))
    assert probe.count("cors_out=$(wget -S -T 8 -O-") == len(origins)
    assert probe.count("grep -iqF") == len(origins)
    assert probe.count("grep -q") == len(origins)
    for origin in origins:
        assert f"Origin: {origin}" in probe
        assert f"Access-Control-Allow-Origin: {origin}" in probe
    assert probe.count(expected_chain) == len(origins)

# Exact Origin expansion for the disposable unpacked MetaMask test extension.
# Never replace the canonical production origin or widen to a wildcard.
TEST_ID = v2.TESTNET_METAMASK_ID


def test_testnet_unpacked_extension_origin_is_single_exact_router(target):
    dynamic = yaml.safe_load(v2.render_v2(target, test_extension_id=TEST_ID))['http']
    extra = f'chrome-extension://{TEST_ID}'
    assert len(dynamic['routers']) == 10
    assert len(dynamic['middlewares']) == 4
    for name, router in dynamic['routers'].items():
        if name.endswith('-metamask-test'):
            assert router['rule'] == f"Host(`{target['route_host']}`) && Header(`Origin`, `{extra}`)"
            assert dynamic['middlewares'][router['middlewares'][0]]['headers']['accessControlAllowOriginList'] == [extra]
    assert 'chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn' in v2.render_v2(target, test_extension_id=TEST_ID)
    assert '*' not in v2.permitted_origins(TEST_ID)


def test_origin_addition_writer_accepts_only_known_previous_route_bytes(target):
    if target["network"] != "testnet":
        pytest.skip("disposable extension origin is testnet-only")
    helper = v2.build_v2_writer_script(target, test_extension_id=TEST_ID)
    current = base64.b64encode(v2.render_v2(target).encode()).decode()
    new = base64.b64encode(v2.render_v2(target, test_extension_id=TEST_ID).encode()).decode()
    assert current in helper and new in helper
    assert helper.count("cmp -s") >= 4
    assert f'Origin: chrome-extension://{TEST_ID}' in helper
    assert 'exit 65' in helper
    revert = v2.build_v2_writer_script(target, test_extension_id=TEST_ID, rollback=True)
    assert current in revert and new in revert and 'exit 65' in revert


@pytest.mark.parametrize('invalid', ['', 'nkbihfbeogaeaoehlefnkodbefgpgknn/', 'a'*31, 'a'*33, 'A'*32, 'https://a'*8, '../other', 'nkbihfbeogaeaoehlefnkodbefgpgknn'])
def test_test_extension_id_must_be_exact_chrome_id(invalid):
    with pytest.raises(v2.RpcBrowserCompatV2Error):
        v2.permitted_origins(invalid)


def test_test_extension_origin_is_refused_for_mainnet_before_private_state():
    with pytest.raises(SystemExit) as result:
        v2.main(['--network', 'mainnet', '--node', 'mainneta-super1',
                 '--host', 'coolify-a', '--test-extension-id', TEST_ID])
    assert result.value.code == 2


def _generated_route_yaml(target):
    """Read the exact policy embedded in the ordinary Mother writer."""
    tree = ast.parse(writer._route_writer_script(target))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(k, ast.Name) and k.id == "DYNAMIC_CONFIG_B64" for k in node.targets
        ):
            return base64.b64decode(ast.literal_eval(node.value)).decode("utf-8")
    raise AssertionError("normal Mother writer has no embedded route config")


def test_canonical_normal_writer_reuses_exact_authorized_policy(target):
    dynamic_text = _generated_route_yaml(target)
    canonical = v2.render_canonical_v2(target)
    assert dynamic_text == canonical
    origins = v2.permitted_origins(v2.canonical_test_extension_id(target["network"]))
    http = yaml.safe_load(dynamic_text)["http"]
    assert len(http["routers"]) == 2 + 2 * len(origins)
    assert len(http["middlewares"]) == len(origins)
    for origin in origins:
        matched = [router for router in http["routers"].values()
                   if router["rule"].endswith(f"Header(`Origin`, `{origin}`)")]
        assert len(matched) == 2
        for router in matched:
            headers = http["middlewares"][router["middlewares"][0]]["headers"]
            assert headers["customResponseHeaders"]["Access-Control-Allow-Origin"] == origin
            assert headers["accessControlAllowOriginList"] == [origin]
    assert all('*' not in str(origin) for origin in origins)
    assert (f"chrome-extension://{TEST_ID}" in dynamic_text) == (target["network"] == "testnet")


def test_deployed_testnet_policy_fingerprint_is_reproduced():
    # Fingerprint reported by Mother's successful 4-origin public RPC mutation.
    target = {"network": "testnet", "controller_id": "coolify-b",
              "route_host": "testnet-rpc.greatlibrary.io",
              "target_url": "http://testnetb-super1:8545"}
    digest = hashlib.sha256(v2.render_canonical_v2(target).encode()).hexdigest()
    assert digest == "1101ada180dd3f647ba8c928511fd4c43ef37ba7236a48d4507c7339758d2120"


def test_testnet_route_rewrite_keeps_existing_four_origins(target):
    if target["network"] != "testnet":
        pytest.skip("testnet lifecycle invariant")
    deployed = v2.render_v2(target, test_extension_id=TEST_ID)
    assert _generated_route_yaml(target) == deployed
    # A second ordinary rewrite must still generate exactly the installed route.
    assert _generated_route_yaml(target) == deployed
    # The guarded maintenance writer must accept both canonical 4-origin and
    # older 3-origin policies without accepting arbitrary route contents.
    helper = v2.build_v2_writer_script(target)
    canonical_b64 = base64.b64encode(deployed.encode()).decode("ascii")
    previous_b64 = base64.b64encode(v2.render_v2(target).encode()).decode("ascii")
    assert f"DYNAMIC_CONFIG_B64 = {canonical_b64!r}" in helper
    assert previous_b64 in helper
    assert "exit 65" in helper


def test_guarded_writer_and_normal_writer_have_identical_target_policy(target):
    helper = v2.build_v2_writer_script(target)
    encoded = base64.b64encode(_generated_route_yaml(target).encode()).decode("ascii")
    assert f"DYNAMIC_CONFIG_B64 = {encoded!r}" in helper
    assert "exit 65" in helper and "cmp -s" in helper


@pytest.mark.parametrize("network", ["", "TESTNET", "bad-network"])
def test_canonical_policy_rejects_unknown_network(network):
    with pytest.raises(v2.RpcBrowserCompatV2Error, match="unsupported RPC network"):
        v2.canonical_test_extension_id(network)


def test_explicit_different_test_extension_id_refused_before_mutation(target):
    if target["network"] != "testnet":
        pytest.skip("testnet override invariant")
    other_id = "abcdefghijklmnopabcdefghijklmnop"
    with pytest.raises(v2.RpcBrowserCompatV2Error, match="canonical network policy"):
        v2.build_v2_writer_script(target, test_extension_id=other_id)
    with pytest.raises(SystemExit) as rejected:
        v2.main(["--network", "testnet", "--node", "testnetb-super1",
                 "--host", "coolify-b", "--test-extension-id", other_id])
    assert rejected.value.code == 2


def test_normal_writer_mainnet_cannot_receive_testnet_origin(target):
    if target["network"] != "mainnet":
        pytest.skip("mainnet isolation invariant")
    assert f"chrome-extension://{TEST_ID}" not in _generated_route_yaml(target)
    with pytest.raises(v2.RpcBrowserCompatV2Error, match="canonical network policy"):
        v2.build_v2_writer_script(target, test_extension_id=TEST_ID)
