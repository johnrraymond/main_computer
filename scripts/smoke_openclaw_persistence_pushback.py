from __future__ import annotations

"""Automated OpenClaw Markdown persistence pushback smoke.

This smoke proves the full non-agent persistence loop without requiring a user to
hand-edit an export:

1. Create or reuse a harmless smoke memory file in the OpenClaw workspace.
2. Export OpenClaw Markdown persistence with high-fidelity source records.
3. Edit the exported JSON payload in memory by appending a unique marker.
4. Apply the edited export back to the workspace with expected-current SHA
   checks, backups, and readback verification.
5. Re-extract the workspace and verify the edited marker is present.
6. Optionally ask the running OpenClaw container to read the mounted
   memory file before and after a restart.

The target file is limited to memory/**/*.md so it exercises the same safe
pushback surface Main Computer will use.
"""

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import random
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

from main_computer.container_runtime import resolve_container_runtime


DEFAULT_TARGET_BASENAME = "main-computer-pushback-smoke.md"
DEFAULT_CONTAINER_WORKSPACE = "/home/node/.openclaw/workspace"


class PushbackSmokeError(RuntimeError):
    """The automated pushback smoke could not prove the persistence loop."""


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def utc_stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def default_memory_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace")).expanduser()


def load_script_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PushbackSmokeError(f"could not load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_companion_modules() -> tuple[Any, Any]:
    script_dir = Path(__file__).resolve().parent
    extractor_path = script_dir / "extract_openclaw_persistence.py"
    apply_path = script_dir / "apply_openclaw_persistence.py"
    if not extractor_path.is_file():
        raise PushbackSmokeError(f"extractor script is missing: {extractor_path}")
    if not apply_path.is_file():
        raise PushbackSmokeError(f"apply script is missing: {apply_path}")
    extractor = load_script_module("extract_openclaw_persistence", extractor_path)
    applier = load_script_module("apply_openclaw_persistence", apply_path)
    return extractor, applier


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_target_relative(raw: str | None) -> str:
    if raw is None or not raw.strip():
        today = utc_now().strftime("%Y-%m-%d")
        return f"memory/{today}-{DEFAULT_TARGET_BASENAME}"
    value = raw.strip().replace("\\", "/")
    candidate = Path(value)
    if candidate.is_absolute():
        raise PushbackSmokeError(f"target relative path cannot be absolute: {raw!r}")
    parts = [part for part in value.split("/") if part]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PushbackSmokeError(f"unsafe target relative path: {raw!r}")
    normalized = "/".join(parts)
    if not (normalized.startswith("memory/") and normalized.lower().endswith(".md")):
        raise PushbackSmokeError("pushback smoke target must be a memory/**/*.md file")
    return normalized


def resolve_target(memory_root: Path, relative_path: str) -> Path:
    root = memory_root.expanduser().resolve()
    target = (root / Path(relative_path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PushbackSmokeError(f"target escapes memory root: {relative_path}") from exc
    return target


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def ensure_target_memory_file(memory_root: Path, relative_path: str) -> tuple[Path, bool]:
    target = resolve_target(memory_root, relative_path)
    if target.exists():
        return target, False
    initial_text = (
        "# Main Computer OpenClaw pushback smoke\n\n"
        "This file is intentionally created by Main Computer's automated "
        "OpenClaw persistence pushback smoke.\n\n"
        "It is safe to keep. Later smoke runs append timestamped markers so "
        "the extract/edit/apply/readback path can be verified without manual "
        "editing.\n"
    )
    atomic_write_text(target, initial_text)
    return target, True


def find_export_record(export: dict[str, Any], relative_path: str) -> dict[str, Any]:
    for record in export.get("files", []):
        if str(record.get("relative_path", "")).replace("\\", "/") == relative_path:
            return record
    raise PushbackSmokeError(f"target file was not present in high-fidelity export: {relative_path}")


def write_export(path: Path, export: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact_export_for_target(export: dict[str, Any], target_record: dict[str, Any]) -> dict[str, Any]:
    result = dict(export)
    result["files"] = [target_record]
    result["stats"] = dict(export.get("stats", {}))
    result["stats"]["file_count"] = 1
    result["stats"]["heading_count"] = len(target_record.get("headings", []))
    result["stats"]["section_count"] = len(target_record.get("sections", []))
    result["stats"]["total_size_bytes"] = target_record.get("size_bytes", 0)
    result["pushback_smoke"] = {
        "generated_at_utc": utc_now_iso(),
        "scope": "single target record edited from a high-fidelity extraction",
    }
    return result


def wait_for_http_ok(url: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:  # pragma: no cover - exercised on live hosts
            last_error = str(exc)
        time.sleep(1.0)
    raise PushbackSmokeError(f"timed out waiting for {url}; last error: {last_error}")


def container_runtime_cli(container_runtime: str | None = None) -> list[str]:
    env = dict(os.environ)
    if container_runtime and container_runtime != "auto":
        env["MAIN_COMPUTER_CONTAINER_RUNTIME"] = container_runtime
    return list(resolve_container_runtime(cwd=Path.cwd(), environ=env, probe=False).container_command)


def run_container_command(args: list[str], *, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        check=False,
    )


def run_docker_exec(args: list[str], *, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Backward-compatible alias for tests/plugins that monkeypatch the old helper name."""

    return run_container_command(args, timeout_s=timeout_s)


def container_exec_command(
    container: str,
    *,
    container_runtime: str | None = None,
    env: dict[str, str] | None = None,
    command: list[str],
) -> list[str]:
    """Build a non-interactive container exec command.

    Plain ``docker exec`` and ``podman exec`` are non-TTY by default unless
    ``-t`` is requested. The ``-T`` no-TTY flag belongs to Compose exec and is
    intentionally avoided here.
    """

    args = [*container_runtime_cli(container_runtime), "exec"]
    for key, value in (env or {}).items():
        args.extend(["-e", f"{key}={value}"])
    args.extend([container, *command])
    return args


def container_probe(
    *,
    container: str,
    marker: str,
    relative_path: str,
    container_workspace: str,
    timeout_s: float,
    container_runtime: str | None = None,
) -> dict[str, Any]:
    js = r"""
const fs = require('fs');
const path = require('path');

const marker = process.env.MAIN_COMPUTER_PUSHBACK_MARKER;
const relativePath = process.env.MAIN_COMPUTER_PUSHBACK_RELATIVE_PATH;
const workspace = process.env.MAIN_COMPUTER_PUSHBACK_CONTAINER_WORKSPACE;

if (!marker || !relativePath || !workspace) {
  console.error('missing probe environment');
  process.exit(3);
}

const target = path.join(workspace, ...relativePath.split('/'));
if (!fs.existsSync(target)) {
  console.error(`target missing: ${target}`);
  process.exit(4);
}

const text = fs.readFileSync(target, 'utf8');
if (!text.includes(marker)) {
  console.error(`marker not found in ${target}: ${marker}`);
  process.exit(5);
}

console.log(JSON.stringify({
  target,
  marker,
  sizeBytes: Buffer.byteLength(text, 'utf8'),
  sha256: require('crypto').createHash('sha256').update(text, 'utf8').digest('hex')
}));
"""
    cmd = container_exec_command(
        container,
        container_runtime=container_runtime,
        env={
            "MAIN_COMPUTER_PUSHBACK_MARKER": marker,
            "MAIN_COMPUTER_PUSHBACK_RELATIVE_PATH": relative_path,
            "MAIN_COMPUTER_PUSHBACK_CONTAINER_WORKSPACE": container_workspace,
        },
        command=["node", "-e", js],
    )
    completed = run_container_command(cmd, timeout_s=timeout_s)
    if completed.returncode != 0:
        raise PushbackSmokeError(
            "container could not read pushed-back memory marker "
            f"(rc={completed.returncode}): {completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise PushbackSmokeError(f"container probe returned non-JSON output: {completed.stdout!r}") from exc


def restart_container(
    container: str,
    *,
    gateway_url: str | None,
    timeout_s: float,
    container_runtime: str | None = None,
) -> None:
    command = [*container_runtime_cli(container_runtime), "restart", container]
    completed = run_container_command(command, timeout_s=timeout_s)
    if completed.returncode != 0:
        runtime = container_runtime or os.environ.get("MAIN_COMPUTER_CONTAINER_RUNTIME") or "container runtime"
        raise PushbackSmokeError(
            f"{runtime} restart failed for {container}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    if gateway_url:
        wait_for_http_ok(gateway_url.rstrip("/") + "/healthz", timeout_s)


def run_pushback_smoke(
    *,
    memory_root: Path,
    export_dir: Path,
    target_relative_path: str | None = None,
    container: str | None = None,
    container_workspace: str = DEFAULT_CONTAINER_WORKSPACE,
    container_runtime: str | None = None,
    restart: bool = False,
    gateway_url: str | None = None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    extractor, applier = load_companion_modules()

    root = memory_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    export_dir = export_dir.expanduser().resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    relative_path = safe_target_relative(target_relative_path)
    target, created_target = ensure_target_memory_file(root, relative_path)

    stamp = utc_stamp()
    suffix = secrets.token_hex(5)
    marker = f"MC_OPENCLAW_PUSHBACK_SMOKE_{stamp}_{suffix}"
    phrase = f"main-computer-pushback-phrase-{secrets.token_hex(4)}"

    original_export = extractor.build_export(root)
    original_record = find_export_record(original_export, relative_path)
    original_text = original_record["text"]
    original_sha = original_record["sha256"]

    append_block = (
        "\n\n"
        f"## Pushback apply smoke {stamp}\n\n"
        f"- Marker: {marker}\n"
        f"- Phrase: {phrase}\n"
        "- Purpose: prove Main Computer can modify a high-fidelity OpenClaw "
        "persistence export and push it back into the mounted Markdown memory layer.\n"
    )

    edited_record = dict(original_record)
    edited_record["text"] = original_text.rstrip() + append_block
    edited_record["pushback_smoke_edit"] = {
        "marker": marker,
        "phrase": phrase,
        "edited_at_utc": utc_now_iso(),
        "expected_current_sha256": original_sha,
        "desired_sha256": sha256_text(edited_record["text"]),
    }

    edited_export = compact_export_for_target(original_export, edited_record)

    original_export_path = export_dir / f"openclaw-pushback-smoke-original-{stamp}.json"
    edited_export_path = export_dir / f"openclaw-pushback-smoke-edited-{stamp}.json"
    write_export(original_export_path, compact_export_for_target(original_export, original_record))
    write_export(edited_export_path, edited_export)

    dry_run = applier.plan_and_apply(
        export_path=edited_export_path,
        memory_root=root,
        dry_run=True,
        verify_after=True,
    )
    if not dry_run.get("ok"):
        raise PushbackSmokeError(f"dry-run pushback failed: {json.dumps(dry_run, indent=2)}")

    apply_result = applier.plan_and_apply(
        export_path=edited_export_path,
        memory_root=root,
        dry_run=False,
        verify_after=True,
    )
    if not apply_result.get("ok"):
        raise PushbackSmokeError(f"pushback apply failed: {json.dumps(apply_result, indent=2)}")

    readback_text = target.read_text(encoding="utf-8")
    if marker not in readback_text or phrase not in readback_text:
        raise PushbackSmokeError("readback file does not contain pushed-back marker and phrase")

    reexport = extractor.build_export(root)
    reexport_record = find_export_record(reexport, relative_path)
    reexport_text = reexport_record["text"]
    if marker not in reexport_text or phrase not in reexport_text:
        raise PushbackSmokeError("re-extracted persistence does not contain pushed-back marker and phrase")

    container_checks: list[dict[str, Any]] = []
    if container:
        before = container_probe(
            container=container,
            marker=marker,
            relative_path=relative_path,
            container_workspace=container_workspace,
            timeout_s=timeout_s,
            container_runtime=container_runtime,
        )
        before["label"] = "before_restart"
        container_checks.append(before)

        if restart:
            restart_container(container, gateway_url=gateway_url, timeout_s=timeout_s, container_runtime=container_runtime)
            after = container_probe(
                container=container,
                marker=marker,
                relative_path=relative_path,
                container_workspace=container_workspace,
                timeout_s=timeout_s,
                container_runtime=container_runtime,
            )
            after["label"] = "after_restart"
            container_checks.append(after)

    proved = [
        "high-fidelity export was generated from the OpenClaw Markdown memory workspace",
        "the exported JSON text payload was edited automatically",
        "the edited export passed expected-current SHA dry-run checks",
        "the edited export was pushed back into the OpenClaw memory workspace",
        "readback SHA/text verification found the pushed-back marker",
        "a fresh high-fidelity re-extraction found the pushed-back marker",
    ]
    if container_checks:
        proved.append("the running OpenClaw container read the pushed-back marker from its mounted workspace")
    if restart and container_checks:
        proved.append("the pushed-back marker survived an OpenClaw container restart")

    return {
        "ok": True,
        "smoke": "openclaw-persistence-pushback",
        "generated_at_utc": utc_now_iso(),
        "memory_root": str(root),
        "target_relative_path": relative_path,
        "target_path": str(target),
        "target_created_for_smoke": created_target,
        "marker": marker,
        "phrase": phrase,
        "exports": {
            "original": str(original_export_path),
            "edited": str(edited_export_path),
        },
        "expected_current_sha256": original_sha,
        "desired_sha256": sha256_text(edited_record["text"]),
        "readback_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "dry_run_stats": dry_run.get("stats"),
        "apply_stats": apply_result.get("stats"),
        "apply_files": apply_result.get("files"),
        "container_checks": container_checks,
        "proved": proved,
    }


def run_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="openclaw-pushback-smoke-") as tmp:
        root = Path(tmp) / "workspace"
        export_dir = Path(tmp) / "exports"
        result = run_pushback_smoke(
            memory_root=root,
            export_dir=export_dir,
            target_relative_path="memory/self-test.md",
            container=None,
            restart=False,
        )
        target_text = (root / "memory" / "self-test.md").read_text(encoding="utf-8")
        if result["marker"] not in target_text:
            raise PushbackSmokeError("self-test marker was not written")
        return {
            "ok": True,
            "self_test": "openclaw-persistence-pushback",
            "result": result,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", type=Path, default=default_memory_root())
    parser.add_argument("--export-dir", type=Path, default=None)
    parser.add_argument("--target-relative-path", default=None)
    parser.add_argument("--container", default=None, help="Container name to probe, e.g. main-computer-openclaw-gateway")
    parser.add_argument(
        "--container-runtime",
        choices=("auto", "docker", "podman"),
        default=os.environ.get("MAIN_COMPUTER_CONTAINER_RUNTIME", "auto"),
        help="Container runtime used for optional exec/restart probes.",
    )
    parser.add_argument("--container-workspace", default=DEFAULT_CONTAINER_WORKSPACE)
    parser.add_argument("--restart-container", action="store_true")
    parser.add_argument("--gateway-url", default=None, help="Gateway URL used to wait for health after container restart")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.self_test:
            result = run_self_test()
        else:
            memory_root = args.memory_root.expanduser().resolve()
            export_dir = (
                args.export_dir.expanduser().resolve()
                if args.export_dir is not None
                else (memory_root.parent / "exports").resolve()
            )
            result = run_pushback_smoke(
                memory_root=memory_root,
                export_dir=export_dir,
                target_relative_path=args.target_relative_path,
                container=args.container,
                container_workspace=args.container_workspace,
                container_runtime=args.container_runtime,
                restart=args.restart_container,
                gateway_url=args.gateway_url,
                timeout_s=args.timeout,
            )

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"OK: {result['smoke'] if 'smoke' in result else result['self_test']}")
            if "target_path" in result:
                print(f"Target: {result['target_path']}")
                print(f"Marker: {result['marker']}")
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "smoke": "openclaw-persistence-pushback",
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
