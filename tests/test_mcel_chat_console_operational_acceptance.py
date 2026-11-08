"""Bounded harness tests; real host native effects are never simulated as PASS here."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.chat_console import build_output_cell, terminal_result_to_parts
from main_computer.config import MainComputerConfig
from main_computer.viewport_server import ViewportServer
from tools import mcel_chat_console_operational_acceptance as acceptance


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    workspace = Path.cwd().resolve()
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(host, data, *, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = Request(host + acceptance.API, data=json.dumps(data).encode(), method="POST", headers=headers)
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_source_contract_and_fingerprint_are_deterministic():
    result = acceptance.check_source()
    assert result["status"] == "PASS"
    assert result["source_sha256"]["main_computer/mcel_chat_console_host.py"]
    assert result["expected_live_html_sha256"] == acceptance.check_source()["expected_live_html_sha256"]


def test_cross_origin_native_eval_denied_before_handler(host):
    payload = {"cell": {"id": "isolated", "type": "terminal", "source": "Write-Output SHOULD_NOT_RUN"}}
    with patch("main_computer.viewport_routes_chat_console.ViewportChatConsoleRoutesMixin._handle_chat_console_cell_evaluate", side_effect=AssertionError("executed")):
        status, answer = _post(host, payload, origin="https://evil.example")
    assert status == 403
    assert answer["ok"] is False
    assert "cross-origin" in answer["error"]


def test_remote_native_eval_denied_before_handler(host):
    payload = {"cell": {"id": "isolated", "type": "terminal", "source": "Write-Output SHOULD_NOT_RUN"}}
    with patch("main_computer.viewport_route_dispatch._hard_halt_client_is_local", return_value=False), \
         patch("main_computer.viewport_routes_chat_console.ViewportChatConsoleRoutesMixin._handle_chat_console_cell_evaluate", side_effect=AssertionError("executed")):
        status, answer = _post(host, payload)
    assert status == 403
    assert answer["ok"] is False
    assert "local-only" in answer["error"]


def test_same_origin_terminal_path_has_real_backend_contract_but_no_native_effect_in_test(host):
    payload = {"cell": {"id": "positive", "type": "terminal", "source": "Write-Output MCEL_TEST_ONLY"}}
    from types import SimpleNamespace
    with patch("main_computer.viewport_routes_chat_console.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="MCEL_TEST_ONLY\n", stderr="")) as mocked:
        status, answer = _post(host, payload, origin=host)
    assert status == 200
    assert answer["ok"] is True
    output = answer["output_cell"]
    assert output["status"] == "ok"
    assert output["source_cell_id"] == "positive"
    assert output["provenance"]["source_cell_type"] == "terminal"
    assert any(part["kind"] == "stdout" and "MCEL_TEST_ONLY" in part["content"] for part in output["parts"])
    assert mocked.call_count == 1  # simulated path does NOT certify native effects


def test_cwd_is_enforced_and_browser_only_code_not_passed_to_backend(host):
    status, data = _post(host, {"cell": {"type": "terminal", "source": "Write-Output SHOULD_NOT_RUN", "cwd": str(Path.cwd().anchor or "/")}})
    assert status == 400 and "inside the local workspace" in data["error"]
    status, data = _post(host, {"cell": {"type": "javascript", "source": "vars.changed = true"}})
    assert status == 400 and "browser code runtime" in data["error"]


def test_scratch_command_bounded_and_contains_finally_cleanup(tmp_path):
    token = "a" * 32
    scratch = tmp_path / f"scratch-{token}.txt"
    command = acceptance.effect_command(scratch, token)
    assert "Set-Content -LiteralPath $scratch" in command
    assert "Get-Content -LiteralPath $scratch" in command
    assert "Get-Process -Id $PID" in command
    assert "Start-Process -FilePath $exe" in command
    assert "Stop-Process -Id $child.Id" in command
    assert "} finally {" in command
    assert "Remove-Item -LiteralPath $scratch" in command
    assert "Invoke-WebRequest" not in command
    with pytest.raises(ValueError):
        acceptance.effect_command(tmp_path / "arbitrary.txt", token)
    with pytest.raises(ValueError):
        acceptance.effect_command(scratch, "unbounded-token")


def test_effect_output_requires_real_receipts_and_exact_lineage():
    token = "c" * 32
    source = acceptance.effect_command(Path(f"scratch-{token}.txt"), token)
    markers = [
        "MCEL_FS_WRITE_READ_PASS:", "MCEL_PROCESS_OBSERVE_PASS:",
        "MCEL_PROCESS_START_PASS:", "MCEL_PROCESS_STOP_PASS:", "MCEL_FS_CLEANUP_PASS:",
    ]
    cell = {"id": "cell-acceptance", "type": "terminal", "source": source}
    parts = terminal_result_to_parts({"command": source, "cwd": ".", "exit_code": 0, "stdout": "\n".join(x + token for x in markers), "stderr": "", "timed_out": False})
    output = build_output_cell(cell, parts, status="ok")
    assert acceptance.validate_effect_output(output, cell["id"], source, token)["status"] == "PASS"
    assert acceptance.validate_effect_output(output, "wrong-cell", source, token)["status"] == "FAIL"
    output["parts"][1]["content"] = "\n".join(x + token for x in markers[:-1])
    assert acceptance.validate_effect_output(output, cell["id"], source, token)["status"] == "FAIL"


def test_hosted_gate_cannot_pass_without_real_windows_effects(tmp_path):
    from argparse import Namespace
    args = Namespace(mode="hosted", host_url="http://127.0.0.1:8765", execute_effects=False, timeout_s=20, report=tmp_path / "report.json")
    report = acceptance.run(args)
    assert report["result"] == "BLOCKED_INCOMPLETE_EVIDENCE"
    assert "--execute-effects" in report["error"]


def test_canonical_http_page_matches_exact_source(host):
    source = acceptance.check_source()
    result = acceptance.check_hosted_routes(host, source)
    assert result["status"] == "PASS"
    assert result["candidate_ir_matches_canonical_source"]


def test_real_semantic_browser_routes_explicit_run_to_backend_with_mocked_powershell(host, tmp_path):
    """Browser and network are real; PowerShell is MOCKED, not operational proof."""
    import re
    from types import SimpleNamespace
    token = "e" * 32
    scratch = tmp_path / f"scratch-{token}.txt"

    real_subprocess_run = subprocess.run

    def simulated_powershell(args, **kwargs):
        # The route renders its semantic IR through the Node.js compiler while
        # serving this page. Do NOT intercept that unrelated subprocess call.
        # Mock only the native terminal invocation, never the compiler.
        if args[:4] != ["powershell", "-NoLogo", "-NoProfile", "-Command"]:
            return real_subprocess_run(args, **kwargs)
        assert {"cwd", "capture_output", "text", "timeout"} <= set(kwargs)
        command = args[4]
        assert "Stop-Process -Id $child.Id" in command
        found = re.search(r"\$token = '([a-f0-9]{32})'", command)
        assert found
        receipt_token = found.group(1)
        markers = [
            "MCEL_FS_WRITE_READ_PASS:", "MCEL_PROCESS_OBSERVE_PASS:",
            "MCEL_PROCESS_START_PASS:", "MCEL_PROCESS_STOP_PASS:", "MCEL_FS_CLEANUP_PASS:",
        ]
        return SimpleNamespace(returncode=0, stdout="\n".join(x + receipt_token for x in markers), stderr="")

    with patch("main_computer.viewport_routes_chat_console.subprocess.run", side_effect=simulated_powershell) as patched:
        try:
            observed = acceptance.run_hosted_browser(host, scratch, token, timeout_s=30)
        except Exception as exc:
            if "ERR_BLOCKED_BY_ADMINISTRATOR" in str(exc):
                pytest.skip("This browser environment forbids Chromium access to loopback HTTP")
            raise
    assert observed["status"] == "PASS"
    assert observed["output_rendered_in_actual_notebook"]
    assert observed["semantic_identity"]["contract"] == acceptance.CONTRACT
    assert observed["stage_without_execution"]
    assert sum(
        call.args[0][:4] == ["powershell", "-NoLogo", "-NoProfile", "-Command"]
        for call in patched.call_args_list
    ) == 1


def test_cross_origin_live_probe_cannot_execute_against_stale_server():
    """A missing guard must FAIL without running terminal code."""
    with patch.object(acceptance, "_http_post", side_effect=[
        (400, {"ok": False, "error": "Cell source is required"}),
        (400, {"ok": False, "error": "Terminal working directory must stay inside the local workspace."}),
        (400, {"ok": False, "error": "Javascript chat cells run in the browser code runtime."}),
    ]) as post:
        result = acceptance.check_negative_boundaries("http://127.0.0.1:8765")
    assert result["status"] == "FAIL"
    assert result["cross_origin_status"] == 400
    assert "Restart" in result["diagnosis"]
    probe = post.call_args_list[0].args[2]
    assert probe["cell"]["type"] == "terminal"
    assert probe["cell"]["source"] == ""


def test_authorization_diagnostics_accept_all_expected_negative_responses():
    with patch.object(acceptance, "_http_post", side_effect=[
        (403, {"ok": False, "error": "Chat Console cross-origin execution denied."}),
        (400, {"ok": False, "error": "Terminal working directory must stay inside the local workspace."}),
        (400, {"ok": False, "error": "Javascript chat cells run in the browser code runtime."}),
    ]):
        result = acceptance.check_negative_boundaries("http://127.0.0.1:8765")
    assert result["status"] == "PASS"
    assert result["diagnosis"] == ""
