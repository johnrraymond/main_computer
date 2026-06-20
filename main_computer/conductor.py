from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONDUCTOR_VERSION = "main-computer-conductor-state-v1"
DEFAULT_RUNTIME_DIR = Path("runtime") / "conductor"
PRIVATE_SUBDIR = "private"
PUBLIC_STATE_FILENAME = "conductor_state.json"
MAX_EVENTS = 200
MAX_JOBS = 500
MAX_WORKER_OUTPUT_BYTES = 1024 * 1024
DUE_SKEW_SECONDS = 1.0
SCRIPT_CATALOG_LIMIT = 500
SCRIPT_OUTPUT_PREVIEW_LIMIT = 12000
SCRIPT_SCAN_DIRECTORIES = (
    "main_computer",
    "scripts",
    "tools",
    "deploy",
    "docker",
    "proto-dev",
    "game_projects",
)
SCRIPT_FILE_EXTENSIONS = {".py", ".ps1", ".sh", ".bat"}
SCRIPT_SKIP_FILENAMES = {"__init__.py", "conftest.py"}
SCRIPT_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "debug_assets",
    "debug_asset_revisions",
    "diagnostics_output",
    "energy_credits",
    "harness_output",
    "node_modules",
    "revision_control",
    "runtime",
}
SCRIPT_SKIP_PATH_PREFIXES = (
    ("tools", "patching"),
)
SCRIPT_SKIP_PATH_PARTS = {
    "new_patch_runs",
}


SCRIPT_DOC_SCAN_DIRECTORIES = (
    ".",
    "main_computer/action_specs",
    "pretty_docs",
    "deploy",
    "docker",
    "tools",
    "proto-dev",
    "contracts",
    "game_projects",
)
SCRIPT_DOC_EXTENSIONS = {".md", ".markdown", ".txt"}
SCRIPT_DOC_REFERENCE_LIMIT = 8
SCRIPT_AREA_BANK = (
    {
        "id": "all",
        "label": "All",
        "description": "Every conductor-runnable script in the current catalog.",
    },
    {
        "id": "documented",
        "label": "Documented commands",
        "description": "Scripts with calling conventions found in repo docs and runbooks.",
    },
    {
        "id": "quarantine-first-pass",
        "label": "Quarantine first pass",
        "description": "Curated scripts that should be safe to try first in an isolated repo/install clone.",
    },
    {
        "id": "dns-ssl-web",
        "label": "DNS / SSL / web",
        "description": "Domain, certificate, mail, website, and publishing operations.",
    },
    {
        "id": "hub-chain-credits",
        "label": "Hub / chain / credits",
        "description": "Hub services, dev chains, contracts, and Compute Credit workflows.",
    },
    {
        "id": "local-platform",
        "label": "Local platform",
        "description": "Local Coolify, Docker, deployment, and workstation platform setup.",
    },
    {
        "id": "temporal-fdb",
        "label": "Temporal / FDB",
        "description": "Temporal lab and FoundationDB smoke/runtime workflows.",
    },
    {
        "id": "ai-worker",
        "label": "AI / worker",
        "description": "Ollama, worker, model, and local AI control workflows.",
    },
    {
        "id": "applications-ui",
        "label": "Applications UI",
        "description": "Viewport, applications shell, browser UI, and WebGL surfaces.",
    },
    {
        "id": "tests-smoke",
        "label": "Tests / smoke",
        "description": "Unit tests, pytest, unittest, and smoke scripts.",
    },
    {
        "id": "developer-tools",
        "label": "Developer tools",
        "description": "Bootstrap, reset, build, maintenance, and repo helper scripts.",
    },
)
SCRIPT_AREA_KEYWORDS = {
    "dns-ssl-web": (
        "cloudflare",
        "dns",
        "domain",
        "mail",
        "mx",
        "ssl",
        "tls",
        "certificate",
        "cert",
        "website",
        "publish",
        "publishing",
        "ingest",
    ),
    "hub-chain-credits": (
        "hub",
        "chain",
        "contract",
        "contracts",
        "credit",
        "credits",
        "escrow",
        "settlement",
        "anvil",
        "foundry",
        "ledger",
        "indexer",
    ),
    "local-platform": (
        "coolify",
        "docker",
        "compose",
        "local-platform",
        "local-prod",
        "deploy/local-platform",
        "docker/",
        "platform",
    ),
    "temporal-fdb": (
        "temporal",
        "foundationdb",
        "fdb",
    ),
    "ai-worker": (
        "ollama",
        "model",
        "models",
        "ai-control",
        "ai_request",
        "ai-request",
        "plex",
        "hub worker",
    ),
    "applications-ui": (
        "main_computer/web",
        "applications/",
        "viewport",
        "browser",
        "webgl",
        "mcel",
    ),
    "tests-smoke": (
        "pytest",
        "unittest",
        "smoke",
        "test_",
        "tests/",
        "verify",
    ),
    "developer-tools": (
        "bootstrap",
        "build",
        "reset",
        "requirements",
        "maintenance",
    ),
}
SCRIPT_QUARANTINE_FIRST_PASS: dict[str, dict[str, Any]] = {
    "tools/git/git_find_used_exts.py": {
        "safety": "Read-only repository extension inventory. Uses git ls-files when available, then falls back to a worktree walk.",
        "notes": "Good first catalog sanity check; no service, Docker, network, or runtime mutation expected.",
        "invocations": [
            {
                "label": "Inventory tracked/unignored files",
                "args": [],
                "timeout_s": 30,
            }
        ],
    },
    "tools/diagnose-dev-control-v2.ps1": {
        "safety": "Host diagnostic that probes process/CIM responsiveness and selected listening ports. It creates and removes a short-lived PowerShell job only.",
        "notes": "Safe for a quarantine host check; it does not start/stop Main Computer services.",
        "invocations": [
            {
                "label": "Probe process and port diagnostics",
                "args": [],
                "timeout_s": 30,
            }
        ],
    },
    "tools/diagnose-dev-control-v3.ps1": {
        "safety": "Focused CIM/WMI hang diagnostic. It creates and removes a short-lived PowerShell job only.",
        "notes": "Useful when the control panel is sluggish; no repo or service mutation expected.",
        "invocations": [
            {
                "label": "Probe CIM/WMI hang behavior",
                "args": [],
                "timeout_s": 30,
            }
        ],
    },
    "scripts/windows/doctor-main-computer-runtime.ps1": {
        "safety": "Read-only WSL runtime doctor when run against the test profile. It inspects WSL state and reports warnings.",
        "notes": "Use -Profile test in quarantine; do not use -Profile prod for this first pass.",
        "invocations": [
            {
                "label": "Doctor test WSL runtime",
                "args": ["-Profile", "test"],
                "timeout_s": 90,
            }
        ],
    },
    "tools/local-platform/generate-websites-compose.py": {
        "safety": "Compose generation check mode. With --check and --no-register-missing it reports stale/missing generated files without writing registrations or compose files.",
        "notes": "Exit code may be non-zero when generated files are stale; that is diagnostic, not a destructive failure.",
        "invocations": [
            {
                "label": "Check generated website compose files",
                "args": ["--repo-root", ".", "--check", "--no-register-missing"],
                "timeout_s": 30,
            }
        ],
    },
    "tools/build_mcel_runtime.py": {
        "safety": "Builds the MCEL runtime bundle to an explicit quarantine output path under runtime/quarantine.",
        "notes": "Writes only the configured output file when used with this invocation.",
        "invocations": [
            {
                "label": "Build MCEL runtime into quarantine output",
                "args": ["--repo-root", ".", "--output", "runtime/quarantine/mcel-runtime.js"],
                "timeout_s": 60,
            }
        ],
    },
    "tools/scheduler_lab/smoke_hub_lab_node_list_builder.py": {
        "safety": "Offline scheduler-lab fixture generator. Writes a node-grid file to an explicit quarantine path.",
        "notes": "No hub, Docker, or Temporal service is required for this fixture-building pass.",
        "invocations": [
            {
                "label": "Generate quarantine scheduler-lab node grid",
                "args": ["runtime/quarantine/scheduler-lab/120-quarantine.jsonl", "--disable-problematic"],
                "timeout_s": 30,
            }
        ],
    },
    "scripts/smoke_protected_mode.py": {
        "safety": "Local protected-mode smoke using an isolated ledger/report path and syscall pressure disabled.",
        "notes": "This is intentionally not live-chain mode and should not touch shared dev/test/prod ledgers with these args.",
        "invocations": [
            {
                "label": "Run isolated protected-mode ledger smoke",
                "args": [
                    "--network", "test",
                    "--ledger-root", "runtime/quarantine/protected-mode-ledger",
                    "--report", "runtime/quarantine/reports/protected-mode.json",
                    "--disable-syscall-pressure",
                ],
                "timeout_s": 120,
            }
        ],
    },
    "scripts/smoke_protected_temporal_flow.py": {
        "safety": "Direct-activity protected Temporal flow smoke using isolated ledger/event-log/report paths.",
        "notes": "Direct-activity mode avoids needing a live Temporal worker for the first quarantine pass.",
        "invocations": [
            {
                "label": "Run direct-activity protected flow smoke",
                "args": [
                    "--execution-mode", "direct-activity",
                    "--ledger-root", "runtime/quarantine/protected-temporal-ledger",
                    "--event-log", "runtime/quarantine/protected-temporal-event-log.jsonl",
                    "--report", "runtime/quarantine/reports/protected-temporal-flow.json",
                ],
                "timeout_s": 120,
            }
        ],
    },
    "main_computer/local_model_prompt_component_v1.py": {
        "safety": "Single local-model prompt boundary. Writes traces to an explicit quarantine output directory.",
        "notes": "Requires a local Ollama-compatible provider; no repo/service install mutation expected.",
        "invocations": [
            {
                "label": "Ask local model for a tiny health response",
                "args": [
                    "--prompt", "Reply with OK.",
                    "--output-dir", "runtime/quarantine/local-model-prompt",
                ],
                "timeout_s": 90,
            }
        ],
    },
}


DNS_RECORD_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX"}
DNS_PROVIDER_MODES = {"cloudflare", "self-hosted"}
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,40}$")


class ConductorError(RuntimeError):
    """Raised when the conductor cannot accept or conduct an operation."""


@dataclass(frozen=True)
class ConductorPaths:
    """Runtime paths used by the local conductor.

    The public state file contains plans, job history, DNS desired-state records,
    and public key fingerprints.  Secret payloads stay in the private directory.
    """

    runtime_root: Path
    state_path: Path
    private_root: Path

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> "ConductorPaths":
        root = repo_root.resolve()
        runtime_root = root / DEFAULT_RUNTIME_DIR
        return cls(
            runtime_root=runtime_root,
            state_path=runtime_root / PUBLIC_STATE_FILENAME,
            private_root=runtime_root / PRIVATE_SUBDIR,
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: Any, *, default_now: bool = False) -> datetime:
    text = str(value or "").strip()
    if not text:
        if default_now:
            return datetime.now(timezone.utc)
        raise ConductorError("run_at is required.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConductorError(f"run_at must be an ISO datetime, got {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ConductorError(f"payload must be JSON serializable: {exc}") from exc


def _safe_identifier(value: Any, *, field: str = "name", default: str = "") -> str:
    text = str(value or default or "").strip()
    if not text:
        raise ConductorError(f"{field} is required.")
    if not SAFE_NAME_RE.fullmatch(text):
        raise ConductorError(f"{field} must contain only letters, numbers, dot, underscore, or hyphen and must start with a letter or number.")
    if ".." in text or "/" in text or "\\" in text:
        raise ConductorError(f"{field} must not contain path traversal or path separators.")
    return text


def _normalize_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    if action not in ConductorWorker.SUPPORTED_ACTIONS:
        raise ConductorError(
            "Unsupported conductor action. Supported actions: "
            + ", ".join(sorted(ConductorWorker.SUPPORTED_ACTIONS))
        )
    return action


def _normalize_chain_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        try:
            return str(int(text, 16))
        except ValueError:
            return text
    return text


def _is_due(run_at: str, *, now: datetime | None = None) -> bool:
    reference = now or datetime.now(timezone.utc)
    due_at = _parse_datetime(run_at, default_now=False)
    return (due_at.timestamp() - reference.timestamp()) <= DUE_SKEW_SECONDS


def _state_skeleton() -> dict[str, Any]:
    now = utc_now()
    return {
        "version": CONDUCTOR_VERSION,
        "created_at": now,
        "updated_at": now,
        "sequence": 0,
        "jobs": {},
        "events": [],
        "dns_records": [],
        "generated_keys": [],
        "notes": [
            "The conductor is a local subprocess-backed control surface for scheduled mutable-state work.",
            "Secret material is not stored in this public state file.",
        ],
    }


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = _state_skeleton()
    if not isinstance(data, dict):
        data = _state_skeleton()
    if data.get("version") != CONDUCTOR_VERSION:
        data["version"] = CONDUCTOR_VERSION
    if not isinstance(data.get("jobs"), dict):
        data["jobs"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not isinstance(data.get("dns_records"), list):
        data["dns_records"] = []
    if not isinstance(data.get("generated_keys"), list):
        data["generated_keys"] = []
    if not isinstance(data.get("sequence"), int):
        data["sequence"] = 0
    data.setdefault("created_at", utc_now())
    data.setdefault("updated_at", utc_now())
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    encoded = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent), prefix=path.name, suffix=".tmp") as handle:
        handle.write(encoded)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def next_id(state: dict[str, Any], prefix: str) -> str:
    state["sequence"] = int(state.get("sequence", 0) or 0) + 1
    return f"{prefix}_{state['sequence']:08d}_{secrets.token_hex(4)}"


def append_event(state: dict[str, Any], *, kind: str, status: str, message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    event = {
        "id": next_id(state, "event"),
        "kind": str(kind or "event"),
        "status": str(status or "ok"),
        "message": str(message or ""),
        "created_at": utc_now(),
        "payload": _json_clone(payload or {}),
    }
    events = list(state.get("events", []))
    events.insert(0, event)
    state["events"] = events[:MAX_EVENTS]
    return event


def _prune_jobs(state: dict[str, Any]) -> None:
    jobs = state.get("jobs")
    if not isinstance(jobs, dict) or len(jobs) <= MAX_JOBS:
        return
    ordered = sorted(
        jobs.items(),
        key=lambda item: str(item[1].get("updated_at") or item[1].get("created_at") or ""),
        reverse=True,
    )
    state["jobs"] = dict(ordered[:MAX_JOBS])


def _worker_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    existing = env.get("PYTHONPATH", "")
    package_parent = Path(__file__).resolve().parents[1]
    paths = [str(repo_root), str(package_parent)]
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["MAIN_COMPUTER_SERVICE_NAME"] = "conductor-worker"
    return env



def _safe_repo_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ConductorError("script is required.")
    path = Path(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConductorError("script must be a plain repository-relative path without traversal.")
    return "/".join(path.parts)


def _path_under_root(root: Path, rel_path: str) -> Path:
    candidate = (root / rel_path).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ConductorError(f"script path escapes repository root: {rel_path}") from exc
    return candidate


def _script_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:32768]
    except OSError:
        return ""


def _python_script_markers(text: str) -> list[str]:
    markers: list[str] = []
    head = text[:200]
    if head.startswith("#!"):
        markers.append("shebang")
    if "if __name__" in text and "__main__" in text:
        markers.append("__main__")
    if re.search(r"def\s+main\s*\(", text):
        markers.append("main()")
    if "argparse" in text:
        markers.append("argparse")
    if "sys.argv" in text:
        markers.append("sys.argv")
    if "subprocess." in text or "Popen(" in text:
        markers.append("subprocess")
    return markers


def _script_risk(rel_path: str, text: str, markers: list[str]) -> str:
    haystack = f"{rel_path}\n{text[:12000]}".lower()
    if any(token in haystack for token in ("delete", "destroy", "rm -rf", "format", "hard-halt", "shutdown", "kill_pid")):
        return "destructive"
    if any(token in haystack for token in ("secret", "private_key", "wallet", "mnemonic", "openssl", "ssl", "token", "password", "credential")):
        return "secret"
    if any(token in haystack for token in ("requests.", "urlopen", "http://", "https://", "scp", "ssh", "cloudflare", "dns", "docker compose", "docker-compose")):
        return "network"
    if any(token in haystack for token in ("write_text", "open(", "subprocess", "os.replace", "shutil.", "docker", "git commit", "git push")):
        return "write"
    return "read-only"


def _script_description(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith(chr(34) * 3) or stripped.startswith(chr(39) * 3):
        quote = stripped[:3]
        end = stripped.find(quote, 3)
        if end > 3:
            first = stripped[3:end].strip().splitlines()[0:1]
            if first:
                return first[0].strip()[:180]
    for line in text.splitlines()[:20]:
        cleaned = line.strip().lstrip("#").strip()
        if cleaned and not cleaned.startswith(("from ", "import ", "if ", "def ", "class ")):
            return cleaned[:180]
    return ""


def _python_module_for_path(repo_root: Path, path: Path) -> str:
    rel = path.relative_to(repo_root).with_suffix("")
    return ".".join(rel.parts)


def _script_command_template(repo_root: Path, path: Path, kind: str) -> list[str]:
    rel = path.relative_to(repo_root).as_posix()
    if kind == "python-module":
        return ["{python}", "-m", _python_module_for_path(repo_root, path)]
    if kind == "python-file":
        return ["{python}", rel]
    if kind == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", rel]
    if kind == "shell":
        return ["bash", rel]
    if kind == "batch":
        return ["cmd", "/c", rel]
    return [rel]


def _script_kind(repo_root: Path, path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            return "python-file"
        if rel.parts and rel.parts[0] == "main_computer" and path.name != "__init__.py":
            return "python-module"
        return "python-file"
    if suffix == ".ps1":
        return "powershell"
    if suffix == ".sh":
        return "shell"
    if suffix == ".bat":
        return "batch"
    return "file"


def _script_path_is_excluded(repo_root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    parts = tuple(part.lower() for part in rel.parts)
    if any(parts[: len(prefix)] == prefix for prefix in SCRIPT_SKIP_PATH_PREFIXES):
        return True
    if any(part in SCRIPT_SKIP_PATH_PARTS for part in parts):
        return True
    directory_parts = parts[:-1] if path.is_file() else parts
    return any(part in SCRIPT_SKIP_DIRS or part.startswith(("diagnostics_output", "harness_output")) for part in directory_parts)


def _script_is_candidate(repo_root: Path, path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in SCRIPT_FILE_EXTENSIONS:
        return False
    if path.name in SCRIPT_SKIP_FILENAMES:
        return False
    if _script_path_is_excluded(repo_root, path):
        return False
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return False
    if path.suffix.lower() != ".py":
        return True
    text = _script_text(path)
    markers = _python_script_markers(text)
    return len(rel.parts) == 1 or "__main__" in markers or "main()" in markers or "argparse" in markers or "sys.argv" in markers or "shebang" in markers


def discover_conductor_scripts(repo_root: Path, *, limit: int = SCRIPT_CATALOG_LIMIT) -> list[dict[str, Any]]:
    """Discover repo scripts, documented calling conventions, and operator areas.

    The catalog is intentionally evidence-based: Python files need script markers
    unless they are top-level helper scripts or are explicitly invoked from docs,
    while shell/PowerShell/batch files are naturally script-shaped.  The conductor
    still requires confirmation before running anything from the catalog.
    """

    root = repo_root.resolve()
    candidates: list[Path] = []
    doc_refs = _document_script_references(root)
    if root.exists():
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and _script_is_candidate(root, child):
                candidates.append(child)
    for directory_name in SCRIPT_SCAN_DIRECTORIES:
        scan_root = root / directory_name
        if not scan_root.exists() or not scan_root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(scan_root):
            current_dir = Path(directory)
            dirnames[:] = [
                name
                for name in sorted(dirnames, key=str.lower)
                if not _script_path_is_excluded(root, current_dir / name)
            ]
            for filename in sorted(filenames, key=str.lower):
                candidate = current_dir / filename
                if _script_is_candidate(root, candidate):
                    candidates.append(candidate)

    for rel in sorted({*doc_refs.keys(), *SCRIPT_QUARANTINE_FIRST_PASS.keys()}):
        path = root / rel
        if path.exists() and path.is_file() and path.suffix.lower() in SCRIPT_FILE_EXTENSIONS:
            candidates.append(path)

    seen: set[str] = set()
    scripts: list[dict[str, Any]] = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        text = _script_text(path)
        markers = _python_script_markers(text) if path.suffix.lower() == ".py" else [path.suffix.lower().lstrip(".")]
        kind = _script_kind(root, path)
        directory = str(Path(rel).parent).replace("\\", "/")
        call_conventions = doc_refs.get(rel, [])
        doc_sources = sorted({str(item.get("doc") or "") for item in call_conventions if item.get("doc")})
        command_template = _script_command_template(root, path, kind)
        areas = _script_areas(rel, text, call_conventions)
        quarantine = _script_quarantine_profile(rel)
        if quarantine and "quarantine-first-pass" not in areas:
            areas.insert(0, "quarantine-first-pass")
        suggested_invocations: list[dict[str, Any]] = []
        if quarantine:
            for invocation in quarantine.get("invocations", []) or []:
                if not isinstance(invocation, dict):
                    continue
                item = dict(invocation)
                item["command"] = _script_quarantine_command(command_template, item)
                suggested_invocations.append(item)
        scripts.append(
            {
                "id": rel,
                "path": rel,
                "name": path.name,
                "directory": "" if directory == "." else directory,
                "kind": kind,
                "extension": path.suffix.lower(),
                "markers": markers,
                "risk": _script_risk(rel, text, markers),
                "description": _script_description(text),
                "command_template": command_template,
                "call_conventions": call_conventions,
                "doc_sources": doc_sources,
                "areas": areas,
                "primary_area": areas[0] if areas else "developer-tools",
                "quarantine_safe": bool(quarantine),
                "quarantine": quarantine,
                "suggested_invocations": suggested_invocations,
                "args": [],
                "confirm_required": True,
            }
        )
    scripts.sort(
        key=lambda item: (
            not bool(item.get("quarantine_safe")),
            item.get("primary_area") != "tests-smoke",
            item["risk"] not in {"read-only", "write"},
            item["directory"],
            item["name"],
        )
    )
    return scripts[: max(1, min(SCRIPT_CATALOG_LIMIT, int(limit or SCRIPT_CATALOG_LIMIT)))]


def conductor_script_by_id(repo_root: Path, script_id: Any) -> dict[str, Any]:
    rel = _safe_repo_relative_path(script_id)
    path = _path_under_root(repo_root.resolve(), rel)
    if not path.exists() or not path.is_file():
        raise ConductorError(f"Unknown conductor script: {rel}")
    catalog = discover_conductor_scripts(repo_root)
    for script in catalog:
        if script.get("id") == rel:
            return script
    raise ConductorError(f"{rel} is not a script-shaped file in the conductor catalog.")


def _script_args(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            return [str(part) for part in shlex.split(value, posix=os.name != "nt")]
        except ValueError as exc:
            raise ConductorError(f"Cannot parse script args safely: {exc}") from exc
    if isinstance(value, list):
        args = [str(item) for item in value]
    else:
        raise ConductorError("script args must be a string or list.")
    if len(args) > 64:
        raise ConductorError("script args are limited to 64 values.")
    if any("\x00" in arg for arg in args):
        raise ConductorError("script args must not contain NUL bytes.")
    return args


def _script_timeout(value: Any) -> int:
    try:
        timeout = int(value or 60)
    except (TypeError, ValueError) as exc:
        raise ConductorError("timeout_s must be a whole number of seconds.") from exc
    return max(1, min(3600, timeout))


def _script_env(repo_root: Path, values: Any) -> dict[str, str]:
    env = _worker_env(repo_root)
    if values in {None, ""}:
        return env
    if not isinstance(values, dict):
        raise ConductorError("env must be an object when provided.")
    for key, value in values.items():
        name = str(key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            raise ConductorError(f"Invalid environment variable name: {key!r}")
        env[name] = str(value)
    return env


def _truncate_output(value: str, limit: int = SCRIPT_OUTPUT_PREVIEW_LIMIT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _command_from_template(template: list[str], *, python_executable: str, args: list[str]) -> list[str]:
    command = [python_executable if part == "{python}" else str(part) for part in template]
    return [*command, *args]


def _script_area_metadata() -> list[dict[str, str]]:
    return [dict(item) for item in SCRIPT_AREA_BANK]


def _normalize_repo_command_path(value: str) -> str:
    token = str(value or "").strip().strip('"').strip("'")
    if not token:
        return ""
    token = token.replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    if token.startswith("/") or re.match(r"^[A-Za-z]:", token):
        return ""
    parts = [part for part in token.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _iter_script_document_paths(repo_root: Path) -> list[Path]:
    root = repo_root.resolve()
    paths: list[Path] = []
    seen: set[str] = set()
    for directory_name in SCRIPT_DOC_SCAN_DIRECTORIES:
        scan_root = root if directory_name == "." else root / directory_name
        if not scan_root.exists() or not scan_root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(scan_root):
            current_dir = Path(directory)
            dirnames[:] = [
                name
                for name in sorted(dirnames, key=str.lower)
                if not _script_path_is_excluded(root, current_dir / name)
            ]
            for filename in sorted(filenames, key=str.lower):
                path = current_dir / filename
                if path.suffix.lower() not in SCRIPT_DOC_EXTENSIONS:
                    continue
                if _script_path_is_excluded(root, path):
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                paths.append(path)
    return paths


def _markdown_code_blocks(text: str) -> list[tuple[str, str, int]]:
    blocks: list[tuple[str, str, int]] = []
    for match in re.finditer(r"^```([^\n`]*)\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL):
        lang = str(match.group(1) or "").strip().lower()
        body = str(match.group(2) or "")
        start_line = text[: match.start(2)].count("\n") + 1
        blocks.append((lang, body, start_line))
    return blocks


def _clean_doc_command_line(line: str) -> str:
    command = str(line or "").strip()
    if not command:
        return ""
    command = re.sub(r"^(?:PS\s+[^>]+>|[$>#])\s*", "", command).strip()
    if not command or command.startswith(("#", "//", "::", "|", "->")):
        return ""
    if command.lower().startswith(("rem ", "note:", "example:", "output:")):
        return ""
    return command


def _split_doc_command(command: str) -> list[str]:
    try:
        return [part.strip().strip('"').strip("'") for part in shlex.split(command, posix=False)]
    except ValueError:
        return []


def _doc_command_target(repo_root: Path, tokens: list[str]) -> tuple[str, list[str]]:
    if not tokens:
        return "", []
    lowered = [token.lower() for token in tokens]
    first = lowered[0]
    python_names = {"python", "python3", "py", "python.exe", "python3.exe", "py.exe"}
    if first in python_names or first.endswith("/python") or first.endswith("/python.exe"):
        if "-m" in lowered:
            module_index = lowered.index("-m")
            if module_index + 1 >= len(tokens):
                return "", []
            module = tokens[module_index + 1].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module):
                return "", []
            target = module.replace(".", "/") + ".py"
            if (repo_root / target).exists():
                return target, tokens[module_index + 2 :]
            return "", []
        for index, token in enumerate(tokens[1:], start=1):
            if token.startswith("-"):
                continue
            target = _normalize_repo_command_path(token)
            if target.lower().endswith(".py") and (repo_root / target).exists():
                return target, tokens[index + 1 :]
        return "", []

    if first in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        for index, token in enumerate(lowered):
            if token == "-file" and index + 1 < len(tokens):
                target = _normalize_repo_command_path(tokens[index + 1])
                if target.lower().endswith(".ps1") and (repo_root / target).exists():
                    return target, tokens[index + 2 :]
        return "", []

    target = _normalize_repo_command_path(tokens[0])
    suffix = Path(target).suffix.lower()
    if suffix in SCRIPT_FILE_EXTENSIONS and (repo_root / target).exists():
        return target, tokens[1:]
    return "", []


def _document_script_references(repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    root = repo_root.resolve()
    references: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for doc_path in _iter_script_document_paths(root):
        try:
            doc_text = doc_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        doc_rel = doc_path.relative_to(root).as_posix()
        for lang, body, start_line in _markdown_code_blocks(doc_text):
            if lang and lang not in {"bash", "bat", "cmd", "computer", "console", "powershell", "ps1", "pwsh", "shell", "sh", "text"}:
                continue
            for offset, raw_line in enumerate(body.splitlines()):
                command = _clean_doc_command_line(raw_line)
                if not command:
                    continue
                tokens = _split_doc_command(command)
                target, args = _doc_command_target(root, tokens)
                if not target:
                    continue
                path = root / target
                if _script_path_is_excluded(root, path):
                    continue
                key = (target, doc_rel, command)
                if key in seen:
                    continue
                seen.add(key)
                bucket = references.setdefault(target, [])
                if len(bucket) >= SCRIPT_DOC_REFERENCE_LIMIT:
                    continue
                bucket.append(
                    {
                        "doc": doc_rel,
                        "line": start_line + offset,
                        "language": lang or "text",
                        "command": command[:500],
                        "args": args[:32],
                    }
                )
    return references


def _script_areas(rel_path: str, text: str, call_conventions: list[dict[str, Any]]) -> list[str]:
    refs_text = "\n".join(
        f"{item.get('doc', '')}\n{item.get('command', '')}"
        for item in call_conventions
        if isinstance(item, dict)
    )
    haystack = f"{rel_path}\n{text[:12000]}\n{refs_text}".lower().replace("\\", "/")
    areas: list[str] = ["documented"] if call_conventions else []
    for area in SCRIPT_AREA_BANK:
        area_id = str(area.get("id") or "")
        if not area_id or area_id == "all":
            continue
        if any(keyword in haystack for keyword in SCRIPT_AREA_KEYWORDS.get(area_id, ())):
            areas.append(area_id)
    if not areas:
        areas.append("developer-tools")
    return areas


def _script_quarantine_profile(rel_path: str) -> dict[str, Any] | None:
    profile = SCRIPT_QUARANTINE_FIRST_PASS.get(rel_path)
    if not profile:
        return None
    invocations: list[dict[str, Any]] = []
    for item in profile.get("invocations", []) or []:
        if not isinstance(item, dict):
            continue
        args = item.get("args", [])
        if isinstance(args, str):
            args = _script_args(args)
        elif isinstance(args, list):
            args = [str(value) for value in args]
        else:
            args = []
        invocations.append(
            {
                "label": str(item.get("label") or "Quarantine invocation"),
                "args": args,
                "timeout_s": _script_timeout(item.get("timeout_s", 60)),
            }
        )
    return {
        "safe": True,
        "safety": str(profile.get("safety") or ""),
        "notes": str(profile.get("notes") or ""),
        "invocations": invocations,
    }


def _script_quarantine_command(command_template: list[str], invocation: dict[str, Any]) -> str:
    command = [
        sys.executable if part == "{python}" else str(part)
        for part in command_template
    ]
    command.extend(str(arg) for arg in invocation.get("args", []) or [])
    return " ".join(shlex.quote(part) for part in command)


def _script_area_bank_with_counts(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {str(area.get("id")): 0 for area in SCRIPT_AREA_BANK}
    counts["all"] = len(scripts)
    for script in scripts:
        for area_id in script.get("areas", []) or []:
            if area_id in counts:
                counts[area_id] += 1
    result: list[dict[str, Any]] = []
    for area in SCRIPT_AREA_BANK:
        item = dict(area)
        item["count"] = counts.get(str(area.get("id")), 0)
        result.append(item)
    return result


class ConductorService:
    """Local scheduler and subprocess dispatcher for mutable operational state."""

    def __init__(self, repo_root: Path, *, python_executable: str | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.paths = ConductorPaths.from_repo_root(self.repo_root)
        self.python_executable = python_executable or os.environ.get("MAIN_COMPUTER_PYTHON") or sys.executable

    def status(self) -> dict[str, Any]:
        state = load_state(self.paths.state_path)
        jobs = list(state.get("jobs", {}).values())
        scheduled = [job for job in jobs if job.get("status") == "scheduled"]
        scheduled.sort(key=lambda job: str(job.get("run_at") or ""))
        scripts = discover_conductor_scripts(self.repo_root)
        return {
            "ok": True,
            "version": CONDUCTOR_VERSION,
            "runtime_root": str(self.paths.runtime_root),
            "state_path": str(self.paths.state_path),
            "private_root": str(self.paths.private_root),
            "actions": sorted(ConductorWorker.SUPPORTED_ACTIONS),
            "worker": {
                "mode": "subprocess",
                "python": self.python_executable,
                "module": "main_computer.conductor_worker",
            },
            "counts": {
                "jobs": len(jobs),
                "scheduled": len(scheduled),
                "dns_records": len(state.get("dns_records", [])),
                "generated_keys": len(state.get("generated_keys", [])),
                "events": len(state.get("events", [])),
                "scripts": len(scripts),
            },
            "next_due": scheduled[0] if scheduled else None,
            "jobs": sorted(jobs, key=lambda job: str(job.get("updated_at") or job.get("created_at") or ""), reverse=True)[:50],
            "events": state.get("events", [])[:50],
            "dns_records": state.get("dns_records", [])[:50],
            "generated_keys": state.get("generated_keys", [])[:50],
            "scripts": scripts,
            "script_areas": _script_area_bank_with_counts(scripts),
        }

    def submit(
        self,
        *,
        action: str,
        payload: dict[str, Any] | None = None,
        run_at: str | None = None,
        confirm: bool = False,
        note: str = "",
    ) -> dict[str, Any]:
        normalized_action = _normalize_action(action)
        clean_payload = _json_clone(payload or {})
        if not isinstance(clean_payload, dict):
            raise ConductorError("payload must be an object.")
        note = str(note or "")

        scheduled_for_future = False
        run_at_iso = ""
        if run_at:
            run_dt = _parse_datetime(run_at)
            run_at_iso = run_dt.isoformat()
            scheduled_for_future = (run_dt.timestamp() - time.time()) > DUE_SKEW_SECONDS

        if scheduled_for_future:
            if not confirm:
                raise ConductorError("Scheduled conductor actions require confirm=true so the future side effect is explicit.")
            state = load_state(self.paths.state_path)
            job_id = next_id(state, "job")
            now = utc_now()
            job = {
                "id": job_id,
                "action": normalized_action,
                "payload": clean_payload,
                "run_at": run_at_iso,
                "confirm": True,
                "status": "scheduled",
                "created_at": now,
                "updated_at": now,
                "note": note,
                "attempts": 0,
                "result": None,
                "error": "",
            }
            state["jobs"][job_id] = job
            append_event(state, kind="job.scheduled", status="scheduled", message=f"Scheduled {normalized_action}.", payload={"job_id": job_id, "run_at": run_at_iso})
            _prune_jobs(state)
            save_state(self.paths.state_path, state)
            return {"ok": True, "scheduled": True, "job": job, "status": self.status()}

        job_id = f"run_{secrets.token_hex(8)}"
        worker_result = self._run_worker(
            {
                "job_id": job_id,
                "action": normalized_action,
                "payload": clean_payload,
                "confirm": bool(confirm),
                "state_path": str(self.paths.state_path),
                "private_root": str(self.paths.private_root),
                "repo_root": str(self.repo_root),
                "note": note,
            }
        )
        return {"ok": bool(worker_result.get("ok")), "scheduled": False, "job": worker_result.get("job"), "worker": worker_result, "status": self.status()}

    def run_due(self, *, now: str | None = None, limit: int = 10) -> dict[str, Any]:
        reference = _parse_datetime(now, default_now=True) if now else datetime.now(timezone.utc)
        limit = max(1, min(100, int(limit or 10)))
        state = load_state(self.paths.state_path)
        due_jobs = [
            job
            for job in state.get("jobs", {}).values()
            if isinstance(job, dict)
            and job.get("status") == "scheduled"
            and _is_due(str(job.get("run_at") or ""), now=reference)
        ]
        due_jobs.sort(key=lambda job: str(job.get("run_at") or ""))
        due_jobs = due_jobs[:limit]
        save_state(self.paths.state_path, state)

        results: list[dict[str, Any]] = []
        for job in due_jobs:
            result = self._run_worker(
                {
                    "job_id": job["id"],
                    "action": job["action"],
                    "payload": job.get("payload") or {},
                    "confirm": True,
                    "state_path": str(self.paths.state_path),
                    "private_root": str(self.paths.private_root),
                    "repo_root": str(self.repo_root),
                    "scheduled_run": True,
                    "note": str(job.get("note") or ""),
                }
            )
            results.append(result)
        return {"ok": all(bool(result.get("ok")) for result in results), "ran": len(results), "results": results, "status": self.status()}

    def _run_worker(self, command: dict[str, Any]) -> dict[str, Any]:
        cmd = [self.python_executable, "-m", "main_computer.conductor_worker"]
        try:
            completed = subprocess.run(
                cmd,
                cwd=self.repo_root,
                input=json.dumps(command, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                env=_worker_env(self.repo_root),
            )
        except subprocess.TimeoutExpired as exc:
            raise ConductorError(f"Conductor worker timed out after {exc.timeout} seconds.") from exc
        stdout = (completed.stdout or "")[:MAX_WORKER_OUTPUT_BYTES]
        stderr = (completed.stderr or "")[:MAX_WORKER_OUTPUT_BYTES]
        try:
            payload = json.loads(stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ConductorError(f"Conductor worker returned non-JSON output: {stdout[:500]!r}; stderr={stderr[:500]!r}") from exc
        if not isinstance(payload, dict):
            raise ConductorError("Conductor worker returned a non-object JSON payload.")
        payload.setdefault("exit_code", completed.returncode)
        payload.setdefault("stderr", stderr)
        if completed.returncode != 0:
            payload["ok"] = False
            payload.setdefault("error", stderr.strip() or f"worker exited with {completed.returncode}")
        return payload


class ConductorWorker:
    """Action implementation that runs inside the conductor subprocess."""

    SUPPORTED_ACTIONS = {
        "dns.record.plan",
        "dns.record.upsert",
        "local.secret.generate",
        "script.run",
        "ssl.key.generate",
    }

    def __init__(self, *, state_path: Path, private_root: Path, repo_root: Path | None = None) -> None:
        self.state_path = state_path
        self.private_root = private_root
        self.repo_root = repo_root

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        action = _normalize_action(command.get("action"))
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        confirm = bool(command.get("confirm"))
        job_id = str(command.get("job_id") or f"run_{secrets.token_hex(8)}")
        if not JOB_ID_RE.fullmatch(job_id):
            job_id = f"run_{secrets.token_hex(8)}"
        note = str(command.get("note") or "")
        state = load_state(self.state_path)

        now = utc_now()
        existing = state.get("jobs", {}).get(job_id)
        if isinstance(existing, dict):
            job = dict(existing)
            job["status"] = "running"
            job["attempts"] = int(job.get("attempts", 0) or 0) + 1
            job["updated_at"] = now
        else:
            job = {
                "id": job_id,
                "action": action,
                "payload": _json_clone(payload),
                "run_at": now,
                "confirm": confirm,
                "status": "running",
                "created_at": now,
                "updated_at": now,
                "note": note,
                "attempts": 1,
                "result": None,
                "error": "",
            }
        state["jobs"][job_id] = job
        append_event(state, kind="job.started", status="running", message=f"Conductor worker started {action}.", payload={"job_id": job_id, "confirm": confirm})
        save_state(self.state_path, state)

        try:
            result = self._execute_action(action, payload, confirm=confirm)
            action_succeeded = bool(result.get("success", True))
            status = "planned" if not result.get("applied") else ("completed" if action_succeeded else "failed")
            message = str(result.get("message") or f"{action} {status}.")
            state = load_state(self.state_path)
            job = dict(state.get("jobs", {}).get(job_id) or job)
            job.update({
                "status": status,
                "updated_at": utc_now(),
                "result": result,
                "error": "" if action_succeeded else message,
            })
            state["jobs"][job_id] = job
            append_event(state, kind=f"{action}.{status}", status=status, message=message, payload={"job_id": job_id, "result": result})
            _prune_jobs(state)
            save_state(self.state_path, state)
            return {"ok": action_succeeded, "job": job, "result": result}
        except Exception as exc:
            state = load_state(self.state_path)
            job = dict(state.get("jobs", {}).get(job_id) or job)
            job.update({"status": "failed", "updated_at": utc_now(), "result": None, "error": str(exc)})
            state["jobs"][job_id] = job
            append_event(state, kind=f"{action}.failed", status="failed", message=str(exc), payload={"job_id": job_id})
            save_state(self.state_path, state)
            return {"ok": False, "job": job, "error": str(exc)}

    def _execute_action(self, action: str, payload: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        if action in {"dns.record.plan", "dns.record.upsert"}:
            return self._action_dns_record(payload, confirm=confirm and action == "dns.record.upsert")
        if action == "local.secret.generate":
            return self._action_local_secret_generate(payload, confirm=confirm)
        if action == "script.run":
            return self._action_script_run(payload, confirm=confirm)
        if action == "ssl.key.generate":
            return self._action_ssl_key_generate(payload, confirm=confirm)
        raise ConductorError(f"Unsupported conductor action: {action}")

    def _action_script_run(self, payload: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        if self.repo_root is None:
            raise ConductorError("repo_root is required for script.run.")
        script_id = payload.get("script") or payload.get("path") or payload.get("id")
        script = conductor_script_by_id(self.repo_root, script_id)
        args = _script_args(payload.get("args", []))
        timeout_s = _script_timeout(payload.get("timeout_s", payload.get("timeout", 60)))
        command = _command_from_template(
            list(script.get("command_template") or []),
            python_executable=sys.executable,
            args=args,
        )
        cwd_value = str(payload.get("cwd") or ".").strip() or "."
        cwd_rel = _safe_repo_relative_path(cwd_value) if cwd_value not in {".", ""} else "."
        cwd = self.repo_root if cwd_rel == "." else _path_under_root(self.repo_root, cwd_rel)
        if not cwd.exists() or not cwd.is_dir():
            raise ConductorError(f"cwd is not a directory: {cwd_rel}")
        plan = {
            "script": script,
            "args": args,
            "cwd": cwd_rel,
            "command": command,
            "timeout_s": timeout_s,
            "output_policy": "stdout/stderr previews are stored in the public conductor job result; do not run secret-printing scripts with stored output enabled.",
        }
        if not confirm:
            return {
                "applied": False,
                "success": True,
                "message": f"Planned script run for {script['id']}.",
                "plan": plan,
            }

        capture_output = bool(payload.get("capture_output", True))
        env = _script_env(self.repo_root, payload.get("env"))
        started_at = utc_now()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_s,
                env=env,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = _truncate_output(exc.stdout or "")
            stderr = _truncate_output(exc.stderr or "")
            return {
                "applied": True,
                "success": False,
                "message": f"Script {script['id']} timed out after {timeout_s} seconds.",
                "script": script,
                "command": command,
                "cwd": cwd_rel,
                "timeout_s": timeout_s,
                "started_at": started_at,
                "finished_at": utc_now(),
                "timed_out": True,
                "returncode": None,
                "stdout_preview": stdout,
                "stderr_preview": stderr,
            }

        stdout = _truncate_output(completed.stdout or "") if capture_output else ""
        stderr = _truncate_output(completed.stderr or "") if capture_output else ""
        success = completed.returncode == 0
        return {
            "applied": True,
            "success": success,
            "message": (
                f"Script {script['id']} completed with exit code 0."
                if success
                else f"Script {script['id']} failed with exit code {completed.returncode}."
            ),
            "script": script,
            "command": command,
            "cwd": cwd_rel,
            "timeout_s": timeout_s,
            "started_at": started_at,
            "finished_at": utc_now(),
            "timed_out": timed_out,
            "returncode": completed.returncode,
            "stdout_preview": stdout,
            "stderr_preview": stderr,
        }

    def _action_dns_record(self, payload: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        record = normalize_dns_record_payload(payload)
        record["revision"] = None
        record["updated_at"] = utc_now()
        record["source"] = "conductor"
        if not confirm:
            return {
                "applied": False,
                "message": f"Planned DNS {record['record_type']} record {record['fqdn']}.",
                "record": record,
                "requires": ["provider adapter or self-hosted nameserver control"],
                "secret_policy": "Provider tokens stay outside the browser and outside conductor public state.",
            }

        state = load_state(self.state_path)
        existing_records = [item for item in state.get("dns_records", []) if isinstance(item, dict)]
        key = (record["zone"], record["record_name"], record["record_type"])
        previous = next(
            (
                item
                for item in existing_records
                if (str(item.get("zone")), str(item.get("record_name")), str(item.get("record_type"))) == key
            ),
            None,
        )
        revision = int(previous.get("revision", 0) if previous else 0) + 1
        record["revision"] = revision
        record["created_at"] = previous.get("created_at") if previous else record["updated_at"]
        filtered = [
            item
            for item in existing_records
            if (str(item.get("zone")), str(item.get("record_name")), str(item.get("record_type"))) != key
        ]
        state["dns_records"] = [record, *filtered][:200]
        append_event(state, kind="dns.record.upserted", status="completed", message=f"Recorded DNS desired state for {record['fqdn']}.", payload={"record": record})
        save_state(self.state_path, state)
        return {
            "applied": True,
            "message": f"Recorded DNS desired state revision {revision} for {record['fqdn']}.",
            "record": record,
        }

    def _action_local_secret_generate(self, payload: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        name = _safe_identifier(payload.get("name") or payload.get("key_name") or "local-secret", field="name")
        purpose = str(payload.get("purpose") or "conductor-local-secret").strip()[:200]
        length = int(payload.get("bytes") or payload.get("length_bytes") or 32)
        if length < 16 or length > 4096:
            raise ConductorError("bytes must be between 16 and 4096.")
        fingerprint_preview = "sha256:<computed-after-generation>"
        if not confirm:
            return {
                "applied": False,
                "message": f"Planned local secret generation for {name}.",
                "key": {
                    "name": name,
                    "purpose": purpose,
                    "bytes": length,
                    "fingerprint": fingerprint_preview,
                },
                "secret_policy": "Secret value will be written only under runtime/conductor/private when confirmed.",
            }

        secret = secrets.token_hex(length)
        import hashlib

        fingerprint = "sha256:" + hashlib.sha256(secret.encode("ascii")).hexdigest()
        self.private_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.private_root, 0o700)
        except OSError:
            pass
        path = self.private_root / f"{name}.json"
        secret_doc = {
            "version": "main-computer-conductor-local-secret-v1",
            "name": name,
            "purpose": purpose,
            "created_at": utc_now(),
            "bytes": length,
            "secret_hex": secret,
            "fingerprint": fingerprint,
        }
        path.write_text(json.dumps(secret_doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        public_record = {
            "id": f"key_{secrets.token_hex(8)}",
            "kind": "local-secret",
            "name": name,
            "purpose": purpose,
            "created_at": secret_doc["created_at"],
            "fingerprint": fingerprint,
            "private_path": str(path),
            "secret_stored": True,
        }
        state = load_state(self.state_path)
        keys = [item for item in state.get("generated_keys", []) if isinstance(item, dict) and item.get("name") != name]
        state["generated_keys"] = [public_record, *keys][:200]
        append_event(state, kind="local.secret.generated", status="completed", message=f"Generated local secret {name}.", payload={"key": public_record})
        save_state(self.state_path, state)
        return {
            "applied": True,
            "message": f"Generated local secret {name}.",
            "key": public_record,
        }

    def _action_ssl_key_generate(self, payload: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        name = _safe_identifier(payload.get("name") or payload.get("key_name") or "ssl-localhost", field="name")
        common_name = _normalize_common_name(payload.get("common_name") or payload.get("domain") or "localhost")
        days = int(payload.get("days") or 30)
        if days < 1 or days > 825:
            raise ConductorError("days must be between 1 and 825.")
        openssl = shutil.which("openssl")
        planned = {
            "name": name,
            "common_name": common_name,
            "days": days,
            "openssl_available": bool(openssl),
            "openssl_path": openssl or "",
            "private_key_path": str(self.private_root / f"{name}.key.pem"),
            "certificate_path": str(self.private_root / f"{name}.cert.pem"),
        }
        if not confirm:
            return {
                "applied": False,
                "message": f"Planned self-signed SSL key/certificate generation for {common_name}.",
                "key": planned,
                "requires": ["openssl on PATH", "local operator confirmation"],
            }
        if not openssl:
            raise ConductorError("openssl is not available on PATH; cannot generate SSL key material without adding a crypto dependency.")
        self.private_root.mkdir(parents=True, exist_ok=True)
        key_path = self.private_root / f"{name}.key.pem"
        cert_path = self.private_root / f"{name}.cert.pem"
        cmd = [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            str(days),
            "-subj",
            f"/CN={common_name}",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        if completed.returncode != 0:
            raise ConductorError(completed.stderr.strip() or f"openssl failed with exit code {completed.returncode}")
        try:
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)
        except OSError:
            pass
        import hashlib

        cert_bytes = cert_path.read_bytes()
        fingerprint = "sha256:" + hashlib.sha256(cert_bytes).hexdigest()
        public_record = {
            "id": f"ssl_{secrets.token_hex(8)}",
            "kind": "ssl-self-signed",
            "name": name,
            "common_name": common_name,
            "days": days,
            "created_at": utc_now(),
            "fingerprint": fingerprint,
            "private_key_path": str(key_path),
            "certificate_path": str(cert_path),
            "secret_stored": True,
        }
        state = load_state(self.state_path)
        keys = [item for item in state.get("generated_keys", []) if isinstance(item, dict) and item.get("name") != name]
        state["generated_keys"] = [public_record, *keys][:200]
        append_event(state, kind="ssl.key.generated", status="completed", message=f"Generated SSL key/certificate for {common_name}.", payload={"key": public_record})
        save_state(self.state_path, state)
        return {
            "applied": True,
            "message": f"Generated SSL key/certificate for {common_name}.",
            "key": public_record,
        }


def _normalize_zone(value: Any) -> str:
    zone = str(value or "").strip().lower().rstrip(".")
    if not zone:
        raise ConductorError("zone is required.")
    labels = zone.split(".")
    if len(labels) < 2:
        raise ConductorError("zone must contain at least two DNS labels.")
    if any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise ConductorError("zone contains an invalid DNS label.")
    return zone


def _normalize_record_name(value: Any) -> str:
    name = str(value if value is not None else "@").strip().lower().rstrip(".")
    if not name:
        name = "@"
    if name == "@":
        return name
    labels = name.split(".")
    if any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise ConductorError("record_name contains an invalid DNS label.")
    return name


def _normalize_common_name(value: Any) -> str:
    text = str(value or "").strip().lower().rstrip(".")
    if not text:
        raise ConductorError("common_name is required.")
    if "*" in text:
        if not text.startswith("*.") or text.count("*") != 1:
            raise ConductorError("wildcard common_name may only start with '*.'.")
        text = text[2:]
    labels = text.split(".")
    if any(not DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise ConductorError("common_name contains an invalid DNS label.")
    return str(value or "").strip().lower().rstrip(".")


def _normalize_ttl(value: Any) -> int:
    try:
        ttl = int(value or 300)
    except (TypeError, ValueError) as exc:
        raise ConductorError("ttl must be a whole number of seconds.") from exc
    if ttl < 60 or ttl > 86400:
        raise ConductorError("ttl must be between 60 and 86400 seconds.")
    return ttl


def normalize_dns_record_payload(payload: dict[str, Any]) -> dict[str, Any]:
    zone = _normalize_zone(payload.get("zone"))
    record_name = _normalize_record_name(payload.get("record_name", payload.get("name", "@")))
    record_type = str(payload.get("record_type") or payload.get("type") or "A").strip().upper()
    if record_type not in DNS_RECORD_TYPES:
        raise ConductorError("record_type must be one of A, AAAA, CNAME, TXT, or MX.")
    record_value = str(payload.get("record_value") or payload.get("value") or "").strip()
    if not record_value:
        raise ConductorError("record_value is required.")
    if len(record_value) > 2048:
        raise ConductorError("record_value is too long.")
    provider_mode = str(payload.get("provider_mode") or "cloudflare").strip().lower()
    if provider_mode not in DNS_PROVIDER_MODES:
        raise ConductorError("provider_mode must be cloudflare or self-hosted.")
    ttl = _normalize_ttl(payload.get("ttl", 300))
    proxied = bool(payload.get("proxied")) if provider_mode == "cloudflare" else False
    owner_wallet = str(payload.get("owner_wallet") or payload.get("wallet_address") or "").strip().lower()
    chain_id = _normalize_chain_id(payload.get("chain_id"))
    fqdn = zone if record_name == "@" else f"{record_name}.{zone}"
    return {
        "provider_mode": provider_mode,
        "zone": zone,
        "record_name": record_name,
        "record_type": record_type,
        "record_value": record_value,
        "ttl": ttl,
        "proxied": proxied,
        "owner_wallet": owner_wallet,
        "chain_id": chain_id,
        "fqdn": fqdn,
    }


def worker_main() -> int:
    try:
        command = json.loads(sys.stdin.read() or "{}")
        if not isinstance(command, dict):
            raise ConductorError("worker command must be a JSON object.")
        state_path = Path(str(command.get("state_path") or "")).expanduser()
        private_root = Path(str(command.get("private_root") or "")).expanduser()
        repo_root_raw = str(command.get("repo_root") or "").strip()
        repo_root = Path(repo_root_raw).expanduser() if repo_root_raw else None
        if not state_path:
            raise ConductorError("state_path is required.")
        if not private_root:
            raise ConductorError("private_root is required.")
        worker = ConductorWorker(state_path=state_path, private_root=private_root, repo_root=repo_root)
        result = worker.execute(command)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
