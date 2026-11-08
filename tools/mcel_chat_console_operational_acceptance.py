"""Live, fail-closed acceptance of canonical MCEL Chat Console native effects.

This is NOT an isolated parity test. Hosted acceptance requires a running local
Windows Main Computer service and a real PowerShell terminal evaluation through
the shipped semantic notebook UI. All native effects are confined to a unique
scratch file and a child PowerShell process started by this acceptance run.

Usage:
  python -u -m tools.mcel_chat_console_operational_acceptance --mode hosted --execute-effects

Without --execute-effects, hosted mode refuses native execution and cannot PASS.
The browser uses an isolated Playwright context: no existing notebook is edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from main_computer.mcel_chat_console_host import compile_chat_console, render_chat_console_page

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path("runtime/reports/mcel-chat-console-operational-acceptance/report.json")
CANONICAL = "/applications/chat-console"
NATIVE_CHILD = CANONICAL + "?mcel-shell-child=1"
CANDIDATE = "/applications/mcel-semantic-chat-console"
API = "/api/applications/chat-console/cell/evaluate"
CONTRACT = "mcel.chat-console.composition.v1"
FINGERPRINT_SOURCES = (
    "tools/fixtures/mcel_semantic_chat_console/application.js",
    "main_computer/mcel_chat_console_host.py",
    "main_computer/web/applications/scripts/chat-console.js",
    "main_computer/viewport_route_dispatch.py",
    "main_computer/viewport_routes_chat_console.py",
    "main_computer/chat_console.py",
    "tools/mcel_chat_console_operational_acceptance.py",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprints(root: Path = ROOT) -> dict[str, str]:
    return {rel: sha256_bytes((root / rel).read_bytes()) for rel in FINGERPRINT_SOURCES}


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def effect_command(scratch: Path, token: str) -> str:
    """Bounded real file write/read/delete and one owned process start/stop.

    PowerShell never calls a model. No modification of user files or processes.
    The child is force-stopped inside finally even if an assertion fails.
    """
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Run token must be a 32-character hexadecimal identifier.")
    if not str(scratch.name).startswith("scratch-") or scratch.suffix != ".txt":
        raise ValueError("Scratch must be a unique .txt file under the acceptance report directory.")
    return "\n".join((
        "$ErrorActionPreference = 'Stop'",
        "$scratch = " + _powershell_quote(str(scratch)),
        "$token = " + _powershell_quote(token),
        "$child = $null",
        "try {",
        "  Set-Content -LiteralPath $scratch -Value $token -Encoding UTF8 -ErrorAction Stop",
        "  if ((Get-Content -LiteralPath $scratch -Raw -ErrorAction Stop).Trim() -ne $token) { throw 'filesystem readback mismatch' }",
        "  Write-Output ('MCEL_FS_WRITE_READ_PASS:' + $token)",
        "  $self = Get-Process -Id $PID -ErrorAction Stop",
        "  if ($self.Id -ne $PID) { throw 'process observation mismatch' }",
        "  Write-Output ('MCEL_PROCESS_OBSERVE_PASS:' + $token)",
        "  $exe = Join-Path $PSHOME 'powershell.exe'",
        "  $child = Start-Process -FilePath $exe -ArgumentList '-NoProfile -NonInteractive -Command \"Start-Sleep -Seconds 30\"' -PassThru -WindowStyle Hidden -ErrorAction Stop",
        "  $child.Refresh()",
        "  if ($child.HasExited) { throw 'child process exited before observation' }",
        "  Write-Output ('MCEL_PROCESS_START_PASS:' + $token)",
        "  Stop-Process -Id $child.Id -Force -ErrorAction Stop",
        "  $child.WaitForExit(5000) | Out-Null",
        "  $child.Refresh()",
        "  if (-not $child.HasExited) { throw 'owned child process did not exit' }",
        "  Write-Output ('MCEL_PROCESS_STOP_PASS:' + $token)",
        "} finally {",
        "  if ($null -ne $child) {",
        "    $child.Refresh()",
        "    if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue }",
        "  }",
        "  if (Test-Path -LiteralPath $scratch) { Remove-Item -LiteralPath $scratch -Force -ErrorAction Stop }",
        "}",
        "if (Test-Path -LiteralPath $scratch) { throw 'scratch cleanup failed' }",
        "Write-Output ('MCEL_FS_CLEANUP_PASS:' + $token)",
    ))


def validate_effect_output(output_cell: dict[str, Any], cell_id: str, source: str, token: str) -> dict[str, Any]:
    parts = output_cell.get("parts") or []
    stdout = "\n".join(str(part.get("content") or "") for part in parts if part.get("kind") == "stdout")
    terminal_parts = [part for part in parts if part.get("kind") == "terminal"]
    expected = [
        "MCEL_FS_WRITE_READ_PASS:", "MCEL_PROCESS_OBSERVE_PASS:",
        "MCEL_PROCESS_START_PASS:", "MCEL_PROCESS_STOP_PASS:", "MCEL_FS_CLEANUP_PASS:",
    ]
    missing = [label for label in expected if (label + token) not in stdout]
    provenance = output_cell.get("provenance") or {}
    exit_code = ((terminal_parts[0].get("metadata") or {}).get("exit_code") if terminal_parts else None)
    return {
        "status": "PASS" if (
            output_cell.get("type") == "output" and output_cell.get("status") == "ok"
            and output_cell.get("source_cell_id") == cell_id
            and provenance.get("source_cell_source_hash") == sha256_bytes(source.encode("utf-8"))
            and provenance.get("source_cell_type") == "terminal"
            and exit_code == 0 and not missing
        ) else "FAIL",
        "missing_markers": missing,
        "exit_code": exit_code,
        "output_status": output_cell.get("status"),
        "output_cell_id": output_cell.get("id"),
        "lineage_source_id_matches": output_cell.get("source_cell_id") == cell_id,
        "lineage_source_sha256_matches": provenance.get("source_cell_source_hash") == sha256_bytes(source.encode("utf-8")),
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8")),
        "stdout_tail": stdout[-1500:],
    }


def _http_get(base: str, path: str) -> bytes:
    with urlopen(base + path, timeout=25) as response:
        if response.status != 200:
            raise RuntimeError(f"GET {path}: HTTP {response.status}")
        return response.read()


def _http_post(base: str, path: str, data: dict[str, Any], *, origin: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    req = Request(base + path, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check_source() -> dict[str, Any]:
    compiled = compile_chat_console(ROOT)
    rendered = render_chat_console_page(ROOT, promoted=True)
    return {
        "status": "PASS" if (
            compiled.get("application", {}).get("appId") == "semantic-chat-console"
            and 'id="mcel-chat-canonical-ir"' in rendered
            and 'id="chat-console-app"' in rendered
            and "MainComputerMCELChatConsole?.compose" in rendered
        ) else "FAIL",
        "source_sha256": fingerprints(),
        "expected_live_html_sha256": sha256_bytes(rendered.encode("utf-8")),
        "semantic_ir_sha256": sha256_bytes(json.dumps(compiled, sort_keys=True).encode("utf-8")),
    }


def check_hosted_routes(base: str, source: dict[str, Any]) -> dict[str, Any]:
    parent = _http_get(base, CANONICAL).decode("utf-8")
    canonical = _http_get(base, NATIVE_CHILD)
    aliases = {path: sha256_bytes(_http_get(base, path)) for path in ("/apps/chat-console", "/app/chat-console")}
    candidate = _http_get(base, CANDIDATE).decode("utf-8")
    marker = re.search(r'<script id="mcel-chat-canonical-ir" type="application/json">(.*?)</script>', candidate, re.S)
    local_ir = compile_chat_console(ROOT)
    cand_ir = json.loads(marker.group(1)) if marker else None
    parent_ok = ('data-mcel-shell-canonical-promoted="true"' in parent and
                 'id="mcel-shell-launcher-panel"' in parent)
    match = sha256_bytes(canonical) == source["expected_live_html_sha256"]
    return {
        "status": "PASS" if parent_ok and match and all(x == sha256_bytes(canonical) for x in aliases.values()) and cand_ir == local_ir else "FAIL",
        "host_shell_promoted": parent_ok,
        "served_canonical_html_sha256": sha256_bytes(canonical),
        "expected_canonical_html_sha256": source["expected_live_html_sha256"],
        "live_html_matches_local_source": match,
        "aliases_sha256": aliases,
        "candidate_ir_matches_canonical_source": cand_ir == local_ir,
    }


def check_negative_boundaries(base: str) -> dict[str, Any]:
    # This probe must remain non-executable even against a stale server that
    # does not yet have the new authorization guard loaded. With an empty
    # command, an unguarded endpoint rejects validation rather than executing.
    cross_origin_probe = {"cell": {"id": "boundary-test", "type": "terminal", "source": ""}}
    cross_code, cross = _http_post(base, API, cross_origin_probe, origin="https://cross-origin.invalid")
    harmless = {"cell": {"id": "boundary-test", "type": "terminal", "source": "Write-Output MCEL_BOUNDARY_SHOULD_NOT_EXECUTE"}}
    # This is an actual API check, not a substitute for a browser-side review.
    cwd_code, cwd_data = _http_post(base, API, {"cell": {**harmless["cell"], "cwd": str(Path(ROOT.anchor))}})
    js_code, js_data = _http_post(base, API, {"cell": {"id": "browser-code", "type": "javascript", "source": "vars.acceptance = 1"}})
    return {
        "status": "PASS" if (
            cross_code == 403 and cross.get("ok") is False
            and cwd_code == 400 and "inside the local workspace" in str(cwd_data.get("error", ""))
            and js_code == 400 and "browser code runtime" in str(js_data.get("error", ""))
        ) else "FAIL",
        "cross_origin_status": cross_code,
        "cross_origin_error": cross.get("error"),
        "outside_workspace_status": cwd_code,
        "outside_workspace_error": cwd_data.get("error"),
        "browser_only_javascript_status": js_code,
        "browser_only_javascript_error": js_data.get("error"),
        "diagnosis": (
            "Cross-origin POST was not rejected with HTTP 403. Restart the Main Computer service "
            "to load viewport_route_dispatch.py, then repeat this gate. If it still fails, "
            "inspect the status/error fields before changing any authorization rule."
            if cross_code != 403 else
            "At least one negative boundary did not match the expected response; "
            "inspect its status/error field."
            if not (cwd_code == 400 and "inside the local workspace" in str(cwd_data.get("error", ""))
                    and js_code == 400 and "browser code runtime" in str(js_data.get("error", "")))
            else ""
        ),
    }


def run_hosted_browser(base: str, scratch: Path, token: str, *, timeout_s: int) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright
    from tools.mcel_chat_console_layout_oracle import _launch

    script = effect_command(scratch, token)
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            # New context: an empty temporary thread store. Does not alter the
            # user's real browser profile, active chats, or saved threads.
            context = browser.new_context(viewport={"width": 1272, "height": 900}, accept_downloads=False)
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            responses: list[dict[str, Any]] = []
            page.on("response", lambda response: responses.append({"url": response.url, "status": response.status}) if urlsplit(response.url).path == API else None)
            page.goto(base + NATIVE_CHILD, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_function("document.body.dataset.mcelChatReady === 'true'", timeout=20000)
            identity = page.evaluate('''() => ({
              ready: document.body.dataset.mcelChatReady,
              contract: document.querySelector('#chat-console-app')?.getAttribute('data-mcel-chat-contract'),
              semanticSource: !!document.querySelector('#mcel-chat-canonical-ir'),
              appId: window.__mcelChatConsole?.appId
            })''')
            if identity != {"ready": "true", "contract": CONTRACT, "semanticSource": True, "appId": "semantic-chat-console"}:
                raise RuntimeError(f"Hosted semantic identity failed: {identity}")
            cell_id = page.evaluate("(source) => MainComputerChatConsole.addCell('terminal', source).id", script)
            cell = page.locator(f'[data-cell-id="{cell_id}"]')
            stage = cell.get_by_role("button", name="Stage", exact=True)
            run = cell.get_by_role("button", name="Run", exact=True)
            if not stage.count() or not run.count():
                raise RuntimeError("Terminal explicit Stage/Run controls not present")
            if scratch.exists():
                raise RuntimeError("Scratch unexpectedly exists before Stage")
            stage.click()
            time.sleep(0.2)
            staged_without_effect = not scratch.exists() and len(responses) == 0
            if not staged_without_effect:
                raise RuntimeError("Stage triggered a native side effect")
            with page.expect_response(lambda response: urlsplit(response.url).path == API, timeout=timeout_s * 1000) as event:
                cell.get_by_role("button", name="Run", exact=True).click()
            api_response = event.value
            response_json = api_response.json()
            output = response_json.get("output_cell") or {}
            evidence = validate_effect_output(output, cell_id, script, token)
            page.wait_for_function(
                "(cellId) => chatConsoleState.cells.some(c => c.type === 'output' && c.source_cell_id === cellId)",
                arg=cell_id, timeout=20000,
            )
            browser_output = page.evaluate("(cellId) => chatConsoleState.cells.find(c => c.type === 'output' && c.source_cell_id === cellId)", cell_id)
            rendered_text = page.locator(f'[data-cell-id="{browser_output["id"]}"]').inner_text()
            ui_ok = (browser_output.get("id") == output.get("id") and all(
                (marker + token) in rendered_text for marker in (
                    "MCEL_FS_WRITE_READ_PASS:", "MCEL_PROCESS_OBSERVE_PASS:",
                    "MCEL_PROCESS_START_PASS:", "MCEL_PROCESS_STOP_PASS:", "MCEL_FS_CLEANUP_PASS:",
                )
            ))
            stage_ok = not scratch.exists()
            result = {
                "status": "PASS" if (
                    api_response.status == 200 and response_json.get("ok") is True
                    and evidence["status"] == "PASS" and ui_ok and staged_without_effect
                    and stage_ok and not errors
                ) else "FAIL",
                "semantic_identity": identity,
                "stage_without_execution": staged_without_effect,
                "api_status": api_response.status,
                "api_ok": response_json.get("ok"),
                "api_error": response_json.get("error"),
                "effect_evidence": evidence,
                "output_rendered_in_actual_notebook": ui_ok,
                "scratch_removed": stage_ok,
                "browser_errors": errors,
                "isolated_thread_id": page.evaluate("() => chatConsoleState?.id || ''"),
            }
            context.close()
            return result
        finally:
            browser.close()


def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "mcel.chat-console.operational-acceptance.v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "host_url": args.host_url,
        "result": "INCOMPLETE",
        "checks": {},
        "notes": "Hosted real Windows effects required. Isolated checks cannot claim operational acceptance.",
    }
    checks = report["checks"]
    try:
        checks["source_contract"] = check_source()
        if checks["source_contract"]["status"] != "PASS":
            raise RuntimeError("Source semantic contract failed")
        if args.mode != "hosted":
            report["result"] = "PASS_SOURCE_ONLY"
            return report
        if not args.execute_effects:
            raise RuntimeError("Live host mode requires --execute-effects; refused to execute silently")
        if os.name != "nt":
            raise RuntimeError("Native PowerShell filesystem/process acceptance requires a Windows host")
        parsed = urlsplit(args.host_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Acceptance refuses a non-loopback or non-HTTP target")
        base = args.host_url.rstrip("/")
        checks["canonical_host"] = check_hosted_routes(base, checks["source_contract"])
        checks["authorization_boundaries"] = check_negative_boundaries(base)
        failed_gates = [name for name, check in checks.items() if check["status"] != "PASS"]
        if failed_gates:
            raise RuntimeError(
                "Host or authorization verification failed; native effects not attempted. "
                "Failed gates: " + ", ".join(failed_gates)
            )
        token = uuid.uuid4().hex
        # Native effects must remain in the repository's dedicated acceptance
        # scratch directory, even if a caller selects a different report path.
        scratch_root = (ROOT / "runtime/reports/mcel-chat-console-operational-acceptance").resolve()
        scratch_root.mkdir(parents=True, exist_ok=True)
        scratch = scratch_root / f"scratch-{token}.txt"
        try:
            checks["real_native_effects"] = run_hosted_browser(base, scratch, token, timeout_s=args.timeout_s)
        finally:
            if scratch.exists():
                # Refuse to delete ambiguous files. Scratch is owned by this run,
                # and a failed cleanup must remain visible as acceptance failure.
                checks["scratch_cleanup"] = {"status": "FAIL", "path": str(scratch), "error": "Unremoved acceptance scratch file"}
            else:
                checks["scratch_cleanup"] = {"status": "PASS"}
        if all(check["status"] == "PASS" for check in checks.values()):
            report["result"] = "PASS_OPERATIONAL_CANONICAL"
            report["notes"] = "Canonical route and real notebook-native filesystem/process execution certified for this exact source and running service."
        else:
            report["result"] = "FAIL_ACCEPTANCE"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["result"] = "FAIL_ACCEPTANCE" if checks and any(c.get("status") == "FAIL" for c in checks.values()) else "BLOCKED_INCOMPLETE_EVIDENCE"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("source", "hosted"), default="source")
    parser.add_argument("--host-url", default="http://127.0.0.1:8765")
    parser.add_argument("--execute-effects", action="store_true", help="Authorize bounded scratch-file and owned child process effects")
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if not args.report.is_absolute():
        args.report = ROOT / args.report
    result = run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("MCEL_CHAT_CONSOLE_OPERATIONAL_ACCEPTANCE", result["result"])
    for name, check in result["checks"].items():
        print(f"  {name}: {check['status']}")
        if check["status"] != "PASS" and name == "authorization_boundaries":
            for field in (
                "cross_origin_status", "cross_origin_error",
                "outside_workspace_status", "outside_workspace_error",
                "browser_only_javascript_status", "browser_only_javascript_error", "diagnosis",
            ):
                print(f"    {field}: {check.get(field)}")
    if result.get("error"):
        print("  error:", result["error"])
    print("  report:", args.report)
    return 0 if result["result"] in ("PASS_SOURCE_ONLY", "PASS_OPERATIONAL_CANONICAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
