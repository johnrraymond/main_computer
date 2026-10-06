from pathlib import Path
import pytest

from tools import mother_preflight_paranoia_validator_set as mod


def test_normalize_validator_set_is_case_insensitive_and_preserves_order():
    values = [
        "0x72151668Fe7A691eAb99c4779D406380C1d0cFD0",
        "0x9B809F05F8D68Da17e697cD6Ab040d4320494611",
    ]
    assert mod._normalize_validator_set(values, "validators") == [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]


def test_rectification_command_uses_live_validator_order_and_actual_nodes():
    live = [
        "0x72151668fe7a691eab99c4779d406380c1d0cfd0",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]
    mapping = {
        live[0]: "mainneta-super2",
        live[1]: "mainnetc-super1",
    }
    argv = mod._rectification_command(
        python_executable="python.exe",
        runtime_state_root=Path(r"C:\Users\subsi\main_computer\runtime\state"),
        network="mainnet",
        topology_path=Path(r"C:\Users\subsi\main_computer\runtime\state\mother\evidence\baseline.json"),
        topology_sha256="a" * 64,
        live_validators=live,
        validator_to_node=mapping,
        timeout=30.0,
        max_response_bytes=4194304,
    )
    assert argv[:3] == ["python.exe", str(mod.REPO_ROOT / "tools" / "mother_deploy.py"), "seal-live-current-topology"]
    assert argv.count("--actual-node") == 2
    assert argv[argv.index("--actual-node") + 1] == "mainneta-super2"
    second = argv.index("--actual-node", argv.index("--actual-node") + 1)
    assert argv[second + 1] == "mainnetc-super1"
    assert "--use-live-topology" in argv
    assert "--write-evidence" in argv


def test_rectification_command_refuses_unknown_live_validator():
    live = ["0x72151668fe7a691eab99c4779d406380c1d0cfd0"]
    assert mod._rectification_command(
        python_executable="python.exe",
        runtime_state_root=Path("runtime/state"),
        network="mainnet",
        topology_path=Path("baseline.json"),
        topology_sha256="b" * 64,
        live_validators=live,
        validator_to_node={},
        timeout=30.0,
        max_response_bytes=4194304,
    ) is None


def test_parser_allows_direct_mode_without_explicit_evidence():
    args = mod.build_parser().parse_args([
        "--runtime-state-root", "runtime/state",
        "--network", "mainnet",
    ])
    assert args.topology_evidence is None
    assert args.acknowledge_topology_evidence_sha256 is None


def test_resolve_topology_arguments_auto_selects_and_self_acknowledges(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime" / "state"
    evidence = runtime_root / "mother" / "evidence" / "deployment-node-remove-finalize" / "latest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"network":"mainnet"}', encoding="utf-8")

    monkeypatch.setattr(mod, "_discover_current_topology_evidence", lambda root, network: evidence)
    args = mod.build_parser().parse_args([
        "--runtime-state-root", str(runtime_root),
        "--network", "mainnet",
    ])

    selected, acknowledged, auto_selected = mod._resolve_topology_arguments(args)
    assert selected == evidence.resolve(strict=False)
    assert acknowledged == mod._file_sha256(evidence)
    assert auto_selected is True


def test_explicit_evidence_without_sha_self_acknowledges(tmp_path):
    runtime_root = tmp_path / "runtime" / "state"
    evidence = runtime_root / "mother" / "evidence" / "deployment-live-current-topology" / "explicit.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"network":"mainnet"}', encoding="utf-8")
    args = mod.build_parser().parse_args([
        "--runtime-state-root", str(runtime_root),
        "--network", "mainnet",
        "--topology-evidence", str(evidence),
    ])

    selected, acknowledged, auto_selected = mod._resolve_topology_arguments(args)
    assert selected == evidence.resolve(strict=False)
    assert acknowledged == mod._file_sha256(evidence)
    assert auto_selected is False


def test_operation_identity_uses_supported_diagnostic_kind():
    operation = mod._operation("mainnet")
    assert operation.operation_kind == "MOTHER-OP-DIAGNOSE"
    assert operation.network == "mainnet"


def test_rpc_uses_established_mother_shared_route_headers(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, _limit):
            return b'{"jsonrpc":"2.0","id":1,"result":"0x28757b0"}'

    def fake_urlopen(request, timeout):
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    result = mod._rpc(
        "https://mainnet-rpc.greatlibrary.io",
        "eth_chainId",
        [],
        timeout=30.0,
        max_response_bytes=1024,
    )
    assert result == "0x28757b0"
    assert captured["headers"]["accept"] == "application/json"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["user-agent"] == "main-computer-mother-validator-rpc-route-preflight/1"



def test_rpc_retries_transient_gateway_errors(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, _limit):
            return b'{"jsonrpc":"2.0","id":1,"result":"0x28757b0"}'

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] < 3:
            raise mod.urllib.error.HTTPError(
                request.full_url, 502, "Bad Gateway", hdrs=None, fp=None
            )
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda value: sleeps.append(value))

    result = mod._rpc(
        "https://mainnet-rpc.greatlibrary.io",
        "eth_chainId",
        [],
        timeout=30.0,
        max_response_bytes=1024,
    )

    assert result == "0x28757b0"
    assert calls["count"] == 3
    assert sleeps == [0.5, 1.0]


def test_rpc_fails_closed_after_bounded_transient_retries(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        raise mod.urllib.error.HTTPError(
            request.full_url, 502, "Bad Gateway", hdrs=None, fp=None
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda value: sleeps.append(value))

    with pytest.raises(mod.ValidatorSetParanoiaError, match=r"after 4 attempt\(s\): HTTP Error 502"):
        mod._rpc(
            "https://mainnet-rpc.greatlibrary.io",
            "eth_chainId",
            [],
            timeout=30.0,
            max_response_bytes=1024,
        )

    assert calls["count"] == 4
    assert sleeps == [0.5, 1.0, 2.0]

def test_classify_blocks_non_validator_primary_nodes_before_reseal():
    state = mod._classify_remediation(
        validator_set_matches=False,
        live_validator_nodes=["mainneta-super2", "mainnetc-super1"],
        live_primary_nodes=[
            "mainneta-super1",
            "mainneta-super2",
            "mainnetc-super1",
            "mainnetc-super2",
        ],
        unmapped_live_validators=[],
    )
    assert state == {
        "status": "blocked-by-non-validator-primary-nodes",
        "reseal_ready": False,
        "non_validator_primary_nodes": ["mainneta-super1", "mainnetc-super2"],
        "validator_nodes_missing_primary": [],
    }


def test_classify_allows_reseal_only_when_primary_nodes_equal_live_validator_nodes():
    state = mod._classify_remediation(
        validator_set_matches=False,
        live_validator_nodes=["mainneta-super2", "mainnetc-super1"],
        live_primary_nodes=["mainneta-super2", "mainnetc-super1"],
        unmapped_live_validators=[],
    )
    assert state["status"] == "ready-to-reseal"
    assert state["reseal_ready"] is True
    assert state["non_validator_primary_nodes"] == []
    assert state["validator_nodes_missing_primary"] == []


def test_classify_blocks_live_validator_without_primary_service():
    state = mod._classify_remediation(
        validator_set_matches=False,
        live_validator_nodes=["mainneta-super2", "mainnetc-super1"],
        live_primary_nodes=["mainneta-super2"],
        unmapped_live_validators=[],
    )
    assert state["status"] == "blocked-by-validator-without-primary-service"
    assert state["reseal_ready"] is False
    assert state["validator_nodes_missing_primary"] == ["mainnetc-super1"]


def test_manual_cleanup_command_targets_guarded_delete_tool_and_nodes():
    argv = mod._manual_cleanup_command(
        python_executable="python.exe",
        runtime_state_root=Path(r"C:\Users\subsi\main_computer\runtime\state"),
        network="mainnet",
        nodes=["mainneta-super1", "mainnetc-super2"],
        rpc_url=None,
        timeout=30.0,
        max_response_bytes=4194304,
    )
    assert argv[:2] == [
        "python.exe",
        str(mod.REPO_ROOT / "tools" / "mother_delete_non_validator_primary_nodes.py"),
    ]
    assert argv.count("--node") == 2
    assert "mainneta-super1" in argv
    assert "mainnetc-super2" in argv
    assert "--execute" in argv
    assert "--yes-i-know-this-deletes-live-node-services" in argv


def test_manual_cleanup_command_renders_runtime_state_root_with_separator():
    argv = mod._manual_cleanup_command(
        python_executable=r"C:\Users\subsi\.venv\scripts\python.exe",
        runtime_state_root=Path(r"C:\Users\subsi\main_computer\runtime\state"),
        network="mainnet",
        nodes=["mainneta-super1", "mainnetc-super2"],
        rpc_url=None,
        timeout=30.0,
        max_response_bytes=4194304,
    )
    rendered = mod._powershell_command(argv)
    assert "--runtime-state-root C:\\Users\\subsi\\main_computer\\runtime\\state" in rendered
    assert "--runtime-state-rootC:" not in rendered
    assert "--node mainneta-super1 --node mainnetc-super2" in rendered


def test_controller_local_rpc_candidates_bypass_public_dns(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        mod,
        "list_coolify_controllers",
        lambda _state: [
            SimpleNamespace(network="mainnet", enabled=True, base_url="http://198.199.75.153:8000", controller_id="coolify-a"),
            SimpleNamespace(network="mainnet", enabled=True, base_url="http://159.203.184.182:8000", controller_id="coolify-c"),
        ],
    )
    candidates = mod._controller_local_rpc_candidates(
        object(),
        network="mainnet",
        route_url="https://mainnet-rpc.greatlibrary.io",
    )
    assert candidates == [
        {
            "url": "http://198.199.75.153/",
            "host_header": "mainnet-rpc.greatlibrary.io",
            "source": "controller-local-traefik-rpc",
            "controller_id": "coolify-a",
        },
        {
            "url": "http://159.203.184.182/",
            "host_header": "mainnet-rpc.greatlibrary.io",
            "source": "controller-local-traefik-rpc",
            "controller_id": "coolify-c",
        },
    ]


def test_rpc_sets_explicit_host_header_for_controller_local_route(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, _limit):
            return b'{"jsonrpc":"2.0","id":1,"result":"0x28757b0"}'

    def fake_urlopen(request, timeout):
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    assert mod._rpc(
        "http://198.199.75.153/",
        "eth_chainId",
        [],
        timeout=30.0,
        max_response_bytes=1024,
        host_header="mainnet-rpc.greatlibrary.io",
    ) == "0x28757b0"
    assert captured["headers"]["host"] == "mainnet-rpc.greatlibrary.io"


def test_observe_live_chain_uses_healthy_controller_route_without_public_fallback(monkeypatch):
    candidates = [
        {"url": "http://198.199.75.153/", "host_header": "mainnet-rpc.greatlibrary.io", "source": "controller-local-traefik-rpc", "controller_id": "coolify-a"},
        {"url": "http://159.203.184.182/", "host_header": "mainnet-rpc.greatlibrary.io", "source": "controller-local-traefik-rpc", "controller_id": "coolify-c"},
    ]
    monkeypatch.setattr(mod, "_controller_local_rpc_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(mod, "_shared_rpc_route_url", lambda _doc: "https://mainnet-rpc.greatlibrary.io")

    calls = []
    def fake_rpc(url, method, params, **kwargs):
        calls.append((url, method))
        if "159.203" in url:
            raise mod.ValidatorSetParanoiaError("HTTP Error 502: Bad Gateway")
        if method == "eth_chainId":
            return "0x28757b0"
        if method == "qbft_getValidatorsByBlockNumber":
            return ["0x72151668fe7a691eab99c4779d406380c1d0cfd0", "0x9b809f05f8d68da17e697cd6ab040d4320494611"]
        if method == "eth_blockNumber":
            return "0xbf70"
        raise AssertionError(method)

    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    result = mod._observe_live_chain(
        object(),
        network="mainnet",
        network_doc={},
        explicit_rpc_url=None,
        timeout=30.0,
        max_response_bytes=1024,
    )
    assert result["authority_source"] == "controller-local-traefik-rpc"
    assert result["authority_controller_id"] == "coolify-a"
    assert result["public_rpc_url"] == "https://mainnet-rpc.greatlibrary.io"
    assert not any(url == "https://mainnet-rpc.greatlibrary.io" for url, _method in calls)
    assert len(result["failures"]) == 1


def test_observe_live_chain_fails_closed_when_healthy_controller_routes_disagree(monkeypatch):
    candidates = [
        {"url": "http://198.199.75.153/", "host_header": "mainnet-rpc.greatlibrary.io", "source": "controller-local-traefik-rpc", "controller_id": "coolify-a"},
        {"url": "http://159.203.184.182/", "host_header": "mainnet-rpc.greatlibrary.io", "source": "controller-local-traefik-rpc", "controller_id": "coolify-c"},
    ]
    monkeypatch.setattr(mod, "_controller_local_rpc_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(mod, "_shared_rpc_route_url", lambda _doc: "https://mainnet-rpc.greatlibrary.io")

    def fake_rpc(url, method, params, **kwargs):
        if method == "eth_chainId":
            return "0x28757b0"
        if method == "eth_blockNumber":
            return "0xbf70"
        if method == "qbft_getValidatorsByBlockNumber":
            if "198.199" in url:
                return ["0x72151668fe7a691eab99c4779d406380c1d0cfd0"]
            return ["0x9b809f05f8d68da17e697cd6ab040d4320494611"]
        raise AssertionError(method)

    monkeypatch.setattr(mod, "_rpc", fake_rpc)
    with pytest.raises(mod.ValidatorSetParanoiaError, match="disagree on validator membership"):
        mod._observe_live_chain(
            object(), network="mainnet", network_doc={}, explicit_rpc_url=None,
            timeout=30.0, max_response_bytes=1024,
        )
