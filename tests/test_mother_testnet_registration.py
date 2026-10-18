"""Mother-owned testnet registration never consults obsolete private state."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

import yaml

from tools.mother.common.deployment_plan import build_starter_deployment_plan
from tools.mother.common.deployment_validator_routes import controller_validator_host
from tools.fdb_control.common.privates import resolve_host_fdb_address
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    prepare_private_state_successor,
    read_private_state,
    replace_verified_private_state,
)

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "mother_identity.py"

# Deterministic read-only Coolify server; tests never contact public infrastructure.
TEST_SERVER_URL = None
TEST_TOKEN = "1|TESTNETTOKENVALUE000000000000000000"


class _CoolifyPrecheckHandler(BaseHTTPRequestHandler):
    calls = []
    project_name = "My first project"
    server_ip = "host.docker.internal"
    server_uuid = "server-b"
    project_uuid = "project-b"
    redirect_projects = False

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        type(self).calls.append((self.command, self.path))
        if type(self).redirect_projects and self.path == "/api/v1/projects":
            self.send_response(302)
            self.send_header("Location", "/login?token=" + TEST_TOKEN)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Authorization") != f"Bearer {TEST_TOKEN}":
            status, payload = 401, {"message": "Unauthenticated"}
        elif self.path == "/api/v1/projects":
            status, payload = 200, [{"uuid": self.project_uuid, "name": self.project_name}]
        elif self.path == "/api/v1/servers/server-b":
            status, payload = 200, {"uuid": self.server_uuid, "name": "localhost", "ip": self.server_ip}
        else:
            status, payload = 404, {"message": "Not found"}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(autouse=True)
def mock_coolify_b(monkeypatch):
    global TEST_SERVER_URL
    _CoolifyPrecheckHandler.calls = []
    _CoolifyPrecheckHandler.project_name = "My first project"
    _CoolifyPrecheckHandler.server_ip = "host.docker.internal"
    _CoolifyPrecheckHandler.server_uuid = "server-b"
    _CoolifyPrecheckHandler.project_uuid = "project-b"
    _CoolifyPrecheckHandler.redirect_projects = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CoolifyPrecheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    TEST_SERVER_URL = f"http://localhost:{server.server_port}"
    monkeypatch.setenv("MC_TESTNET_COOLIFY_B_API_TOKEN", TEST_TOKEN)
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        TEST_SERVER_URL = None



def op(label, network="mainnet"):
    return OperationIdentity(operation_id=label, request_id=label, network=network,
                             operation_kind="MOTHER-OP-IDENTITY-ROTATION")


def initialize(tmp_path):
    root = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    document = {"kind": "main_computer.mother.private_state.v1", "schema_version": 1,
                "networks": {"mainnet": {"chain_id": 42424240,
                                         "coolify": {"controllers": {"coolify-a": {"enabled": True}},
                                                     "mutation_authority": "observe-only"},
                                         "wallets": {"deployer": {"private_key": "0x" + "11" * 32}},
                                         "other_mainnet_identity": "sentinel"}}}
    initial = prepare_private_state_bootstrap(paths, document,
         updated_at="2026-10-17T00:00:00Z", updated_by_action_id="bootstrap", operation=op("bootstrap"))
    install_verified_private_state(paths, initial, None, operation=op("bootstrap"))
    first = read_private_state(paths, operation=op("read-first"))
    successor = prepare_private_state_successor(first, document, updated_at="2026-10-17T00:01:00Z",
         updated_by_action_id="advance", operation=op("advance"))
    replace_verified_private_state(paths, successor, first.binding, operation=op("advance"))
    return root, paths, read_private_state(paths, operation=op("read-second"))


def run(root, *, write=False, generation=None, env=None, extra_args=()):
    args = [sys.executable, str(CLI), "register-testnet", "--runtime-state-root", str(root),
            "--controller-url", "http://144.126.212.9:8000/",
            "--public-ip", "144.126.212.9", "--vpn-ip", "10.124.0.3",
            "--droplet-hostname", "coolify-b",
            "--project-uuid", "project-b", "--server-uuid", "server-b",
            "--updated-at", "2026-10-17T00:02:00Z", "--operation-id", "register-testnet-test"]
    if generation is not None:
        args.extend(["--acknowledge-generation", str(generation)])
    if write:
        args.append("--write")
    args.extend(["--controller-url", TEST_SERVER_URL])
    args.extend(extra_args)
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False, env=env)


def test_registration_is_read_only_without_write_and_redacts_secrets(tmp_path):
    root, paths, before = initialize(tmp_path)
    files_before = [(p.relative_to(root), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "write performed: no" in proc.stdout
    assert "secrets printed: 0" in proc.stdout
    assert "vpn_ip: 10.124.0.3" in proc.stdout
    assert "Coolify precheck: PASS" in proc.stdout
    assert "VPN 10.124.0.3 is not independently attested" in proc.stdout
    assert "1|TESTNETTOKEN" not in proc.stdout + proc.stderr
    assert _CoolifyPrecheckHandler.calls == [
        ("GET", "/api/v1/projects"), ("GET", "/api/v1/servers/server-b"),
    ]
    assert "droplet_hostname: coolify-b" in proc.stdout
    assert files_before == [(p.relative_to(root), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]


def test_registers_one_isolated_testnet_and_preserves_mainnet(tmp_path):
    root, paths, before = initialize(tmp_path)
    initial_doc = yaml.safe_load(before.document_bytes)
    env = dict(os.environ, MC_TESTNET_COOLIFY_B_API_TOKEN="1|TESTNETTOKENVALUE000000000000000000")
    # --write automatically uses the verified state binding observed by Mother;
    # callers must not have to supply an expected generation number.
    proc = run(root, write=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "current Mother generation: 2" in proc.stdout
    assert "installed Mother generation: 3" in proc.stdout
    assert "TESTNETTOKEN" not in proc.stdout
    after = read_private_state(paths, operation=op("read-after", "testnet"))
    assert after.binding.generation == 3
    state = yaml.safe_load(after.document_bytes)
    assert state["networks"]["mainnet"] == initial_doc["networks"]["mainnet"]
    testnet = state["networks"]["testnet"]
    assert testnet["chain_id"] == 42424241
    assert list(testnet["deployment"]["targets"]) == ["testnetb-super1"]
    assert list(testnet["coolify"]["controllers"]) == ["coolify-b"]
    controller = testnet["coolify"]["controllers"]["coolify-b"]
    assert controller["public_ip"] == "144.126.212.9"
    assert controller["vpn_ip"] == "10.124.0.3"
    assert controller["droplet_hostname"] == "coolify-b"
    assert controller["project_name"] == "My first project"
    assert controller["project_name_hint"] == "My first project"
    assert resolve_host_fdb_address(state, "testnet", "coolify-b")[0] == "10.124.0.3"
    assert controller_validator_host(after, network="testnet", controller_id="coolify-b")[0] == "10.124.0.3"
    assert testnet["wallets"]["captain"]["private_key"] != initial_doc["networks"]["mainnet"]["wallets"]["deployer"]["private_key"]
    assert len(testnet["wallets"]["hub_admin_reserve"]) == 14
    plan = build_starter_deployment_plan(after, network="testnet")
    assert plan["network"] == "testnet" or plan["network_key"] == "testnet"
    # Repeated execution must not overwrite the already reserved network.
    twice = run(root, write=True, env=env)
    assert twice.returncode != 0
    assert read_private_state(paths, operation=op("read-again")).binding == after.binding


def test_registration_rejects_missing_token_and_stale_generation(tmp_path):
    root, paths, before = initialize(tmp_path)
    env = dict(os.environ)
    env.pop("MC_TESTNET_COOLIFY_B_API_TOKEN", None)
    missing = run(root, write=True, generation=2, env=env)
    assert missing.returncode != 0
    env["MC_TESTNET_COOLIFY_B_API_TOKEN"] = "token"
    stale = run(root, write=True, generation=1, env=env)
    assert stale.returncode != 0
    assert read_private_state(paths, operation=op("read-after-failure")).binding == before.binding


def test_registration_rejects_missing_or_unusable_controller_coordinates(tmp_path):
    root, paths, before = initialize(tmp_path)
    # All invalid requests must fail before any identity is generated or state is written.
    cases = (
        ("--public-ip", "10.124.0.3"),  # private address is not a public controller address
        ("--public-ip", "144.126.212.8"),  # mismatches controller URL
        ("--public-ip", "not-an-ip"),
        ("--vpn-ip", "144.126.212.9"),  # publicly routable address is not the private route
        ("--vpn-ip", "127.0.0.1"),
        ("--vpn-ip", "0.0.0.0"),
        ("--vpn-ip", "10.0.0.0/8"),
        ("--droplet-hostname", "localhost/invalid"),
        ("--droplet-hostname", ""),
    )
    env = dict(os.environ, MC_TESTNET_COOLIFY_B_API_TOKEN="test-token-do-not-print")
    for option, bad_value in cases:
        proc = run(root, write=True, generation=2, env=env,
                   extra_args=(option, bad_value))
        assert proc.returncode != 0, (option, bad_value, proc.stdout, proc.stderr)
        assert "test-token-do-not-print" not in proc.stdout + proc.stderr
        assert read_private_state(paths, operation=op("read-invalid")).binding == before.binding


def test_missing_new_required_arguments_fails_closed(tmp_path):
    root, paths, before = initialize(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(CLI), "register-testnet",
         "--runtime-state-root", str(root), "--controller-url", "http://144.126.212.9:8000/",
         "--project-uuid", "project-b", "--server-uuid", "server-b", "--write",
         "--acknowledge-generation", "2"],
        cwd=ROOT, text=True, capture_output=True, check=False,
        env=dict(os.environ, MC_TESTNET_COOLIFY_B_API_TOKEN="test-token-do-not-print"),
    )
    assert proc.returncode != 0
    assert "--public-ip" in proc.stderr
    assert "--vpn-ip" in proc.stderr
    assert "--droplet-hostname" in proc.stderr
    assert read_private_state(paths, operation=op("read-after-missing")).binding == before.binding


def test_precheck_requires_token_even_for_read_only_preview(tmp_path):
    root, paths, before = initialize(tmp_path)
    env = dict(os.environ)
    env.pop("MC_TESTNET_COOLIFY_B_API_TOKEN", None)
    proc = run(root, env=env)
    assert proc.returncode != 0
    assert "API token environment variable is required" in proc.stderr
    assert _CoolifyPrecheckHandler.calls == []
    assert read_private_state(paths, operation=op("read-no-token")).binding == before.binding


def test_precheck_rejects_bad_credentials_and_wrong_project_or_server(tmp_path):
    root, paths, before = initialize(tmp_path)
    wrong_token = run(root, env=dict(os.environ, MC_TESTNET_COOLIFY_B_API_TOKEN="bad-token"))
    assert wrong_token.returncode != 0
    assert "HTTP 401" in wrong_token.stderr
    assert "bad-token" not in wrong_token.stdout + wrong_token.stderr

    _CoolifyPrecheckHandler.project_uuid = "wrong-project"
    wrong_project = run(root)
    assert wrong_project.returncode != 0
    assert "project UUID not found" in wrong_project.stderr
    _CoolifyPrecheckHandler.project_uuid = "project-b"

    _CoolifyPrecheckHandler.server_uuid = "unexpected-server"
    wrong_server = run(root)
    assert wrong_server.returncode != 0
    assert "server UUID differs" in wrong_server.stderr
    assert read_private_state(paths, operation=op("read-after-precheck-failure")).binding == before.binding


def test_precheck_rejects_mismatched_server_address_and_warns_unverifiable_hostname(tmp_path):
    root, paths, before = initialize(tmp_path)
    _CoolifyPrecheckHandler.server_ip = "192.0.2.77"
    mismatch = run(root, write=True, generation=2)
    assert mismatch.returncode != 0
    assert "SSH address differs" in mismatch.stderr
    assert read_private_state(paths, operation=op("read-mismatch")).binding == before.binding
    _CoolifyPrecheckHandler.server_ip = "10.124.0.3"
    accepted = run(root)
    assert accepted.returncode == 0, accepted.stderr
    assert "does not prove its live interface assignment" in accepted.stdout
    assert "OS hostname" in accepted.stdout


def test_precheck_rejects_remote_plaintext_http_without_explicit_override(tmp_path):
    root, paths, before = initialize(tmp_path)
    # Run parser validation before a network request to this remote URL.
    unsafe = run(root, extra_args=("--controller-url", "http://144.126.212.9:8000/"))
    assert unsafe.returncode != 0
    assert "--allow-insecure-http-api" in unsafe.stderr
    assert read_private_state(paths, operation=op("read-unsafe")).binding == before.binding


def test_precheck_normalizes_trailing_slash_and_stores_canonical_origin(tmp_path):
    root, paths, before = initialize(tmp_path)
    preview = run(root, extra_args=("--controller-url", TEST_SERVER_URL + "/"))
    assert preview.returncode == 0, preview.stderr
    assert "Coolify precheck: PASS" in preview.stdout
    assert _CoolifyPrecheckHandler.calls == [
        ("GET", "/api/v1/projects"),
        ("GET", "/api/v1/servers/server-b"),
    ]
    written = run(root, write=True, generation=2,
                  extra_args=("--controller-url", TEST_SERVER_URL + "/"))
    assert written.returncode == 0, written.stderr
    document = yaml.safe_load(read_private_state(paths, operation=op("read-canonical")).document_bytes)
    assert document["networks"]["testnet"]["coolify"]["controllers"]["coolify-b"]["url"] == TEST_SERVER_URL
    assert document["networks"]["mainnet"] == yaml.safe_load(before.document_bytes)["networks"]["mainnet"]


def test_precheck_rejects_redirect_with_safe_reason_and_no_identity_mutation(tmp_path):
    root, paths, before = initialize(tmp_path)
    _CoolifyPrecheckHandler.redirect_projects = True
    proc = run(root, write=True, generation=2,
               extra_args=("--controller-url", TEST_SERVER_URL + "/"))
    assert proc.returncode != 0
    assert "HTTP 302 redirect (interactive login page)" in proc.stderr
    assert "redirects are not followed" in proc.stderr
    assert TEST_TOKEN not in proc.stdout + proc.stderr
    assert "?token" not in proc.stdout + proc.stderr
    assert _CoolifyPrecheckHandler.calls == [("GET", "/api/v1/projects")]
    assert read_private_state(paths, operation=op("read-uncommitted")).binding == before.binding
