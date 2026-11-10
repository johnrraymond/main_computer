"""P5.4a read-only legacy Hub deletion inventory.

Run from the repository root::

    python -m tools.hub_legacy_deletion_inventory

This performs static source inspection only: no HTTP calls, no Worker launch,
no FoundationDB access, no account mutation and no file writes.
It does NOT authorize deleting code or claim that string searches prove reachability.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterator

# These are the source groups where Hub references can affect deployment,
# diagnostics and regression acceptance. Ignore third-party/vendor sources.
SOURCE_DIRS = ("main_computer", "tools", "scripts", "tests")
# Match the directories excluded by Test-RepoPathAllowed in
# export-main-computer-test.ps1. Prune them BEFORE entering them, not after
# rglob() has already enumerated their files. The source roots above are all
# explicitly included by $mainExportItems in the exporter.
EXPORT_EXCLUDED_DIR_NAMES = frozenset({
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", ".venv", "venv", "chat_console_shared_variables", "spreadsheets",
    ".tmp", "harness_output", "harness_output_pretty_docs",
    "harness_output_game_editor", "migration", "release_reports", "debug_assets",
    ".main_computer_browser_profile", "cache", "site-packages",
})
# Exporter has additional path-specific exceptions, rather than excluding all
# directories with these names.
EXPORT_EXCLUDED_PREFIXES = ("tools/patching", "tools/documentation/plan-")


def exported_python_sources(repo: Path, progress: Callable[[str], None] | None = None) -> Iterator[Path]:
    """Walk just exported source roots, pruning excluded subtrees *in place*.

    ``Path.rglob`` yields after descending and cannot prevent expensive
    traversal of local dependencies or generated directories on Windows.
    ``os.walk(topdown=True)`` avoids visiting those excluded directories.
    """
    for folder in SOURCE_DIRS:
        directory = repo / folder
        if not directory.is_dir():
            continue
        if progress:
            progress(f"Scanning exported source root: {folder}/")
        for parent, dirs, files in os.walk(directory, topdown=True, followlinks=False):
            parent_path = Path(parent)
            def allowed_directory(name: str) -> bool:
                child = parent_path / name
                rel = child.relative_to(repo).as_posix().lower()
                if name.lower() in EXPORT_EXCLUDED_DIR_NAMES or name.lower().endswith(".egg-info"):
                    return False
                if any(rel == prefix or rel.startswith(prefix + "/")
                       for prefix in EXPORT_EXCLUDED_PREFIXES if not prefix.endswith("-")):
                    return False
                if any(rel.startswith(prefix) for prefix in EXPORT_EXCLUDED_PREFIXES if prefix.endswith("-")):
                    return False
                return not child.is_symlink()
            dirs[:] = sorted((name for name in dirs if allowed_directory(name)), key=str.lower)
            for name in sorted(files, key=str.lower):
                if not name.lower().endswith(".py"):
                    continue
                path = parent_path / name
                if path.is_symlink():
                    continue
                yield path
IMPORTANT_SOURCE_FILES = (
    "main_computer/cli.py", "main_computer/hub_cli_live_session.py",
    "main_computer/exp_fdb_hub.py", "main_computer/exp_fdb_hub_state.py",
    "main_computer/viewport_routes_chat_console.py", "main_computer/router.py",
    "main_computer/stable_hub.py",
)
REQUIRED_MODULES = (
    "hub.py", "hub_facade_helpers.py", "hub_registry_shared.py", "hub_registry_json.py",
    "hub_http_shared.py", "hub_http_runtime.py", "hub_http_legacy_support.py",
    "hub_http_legacy_handler.py", "hub_worker_http.py", "hub_worker_client.py",
    "exp_fdb_hub.py", "exp_fdb_hub_state.py", "dev_test_hub_legacy_routes.py",
)
PROTOCOL_MARKERS = {
    "legacy_worker_rest_register": "/api/hub/v1/workers/register",
    "legacy_worker_rest_heartbeat": "/api/hub/v1/workers/heartbeat",
    "legacy_worker_rest_poll": "/api/hub/v1/workers/poll",
    "legacy_worker_rest_results": "/api/hub/v1/workers/results",
    "legacy_chat_session_start": "/api/hub/sessions/start",
    "legacy_chat_session_chat": "/api/hub/sessions/chat",
    "dev_test_safe_chat_simulation": "/api/hub/remote-overflow/safe-chat",
}


def inventory(repo: Path | None = None, *, progress: Callable[[str], None] | None = None) -> dict[str, object]:
    repo = Path(repo or Path(__file__).resolve().parents[1]).resolve()
    missing = [name for name in REQUIRED_MODULES if not (repo / "main_computer" / name).is_file()]
    if missing:
        raise FileNotFoundError(f"P5.3i modules missing: {', '.join(missing)}")

    imports: dict[str, set[str]] = defaultdict(set)
    attrs: dict[str, set[str]] = defaultdict(set)
    module_uses: dict[str, set[str]] = defaultdict(set)
    protocol_uses: dict[str, set[str]] = defaultdict(set)
    python_files = 0
    parse_failures = []
    important_edges: dict[str, set[str]] = defaultdict(set)

    for path in exported_python_sources(repo, progress=progress):
        rel = path.relative_to(repo).as_posix()
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        python_files += 1
        if progress and python_files % 250 == 0:
            progress(f"Scanned {python_files} exported Python source files...")
        for label, marker in PROTOCOL_MARKERS.items():
            if marker in source:
                protocol_uses[label].add(rel)
        # Only sources importing the historical Hub facade (or one of
        # the explicitly checked owners) need AST parsing. This avoids
        # slowing the audit on thousands of unrelated source files.
        if "main_computer.hub" not in source and rel not in IMPORTANT_SOURCE_FILES:
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError) as exc:
            parse_failures.append({"file": rel, "error_type": type(exc).__name__})
            continue
        module_aliases = set()
        if rel in IMPORTANT_SOURCE_FILES:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and (
                    node.module == "main_computer.hub" or
                    node.module.startswith("main_computer.hub_") or
                    node.module.startswith("main_computer.exp_fdb_") or
                    node.module.startswith("main_computer.stable_hub") or
                    node.module == "main_computer.remote_overflow" or
                    node.module == "main_computer.providers.hub"
                ):
                    important_edges[rel].add(node.module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "main_computer.hub" or alias.name.startswith("main_computer.hub_"):
                            important_edges[rel].add(alias.name)
        package_aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "main_computer.hub":
                for alias in node.names:
                    imports[alias.name].add(rel)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "main_computer.hub":
                        module_uses["module_import"].add(rel)
                        if alias.asname:
                            module_aliases.add(alias.asname)
                        else:
                            package_aliases.add("main_computer")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in module_aliases:
                    attrs[node.attr].add(rel)
                if (isinstance(node.value, ast.Attribute) and node.value.attr == "hub"
                        and isinstance(node.value.value, ast.Name)
                        and node.value.value.id in package_aliases):
                    attrs[node.attr].add(rel)

    def source_kind(rel: str) -> str:
        return rel.split("/", 1)[0]

    symbol_imports = []
    for symbol in sorted(set(imports) | set(attrs)):
        paths = imports.get(symbol, set()) | attrs.get(symbol, set())
        symbol_imports.append({
            "symbol": symbol,
            "production_callers": sorted(p for p in paths if source_kind(p) == "main_computer"),
            "operator_script_callers": sorted(p for p in paths if source_kind(p) == "scripts"),
            "verification_and_test_callers": sorted(p for p in paths if source_kind(p) in {"tools", "tests"}),
        })

    protocols = [
        {"route_family": label,
         "main_computer_files": sorted(p for p in protocol_uses.get(label, set()) if source_kind(p) == "main_computer"),
         "scripts_and_tools": sorted(p for p in protocol_uses.get(label, set()) if source_kind(p) in {"scripts", "tools"}),
         "test_file_count": sum(source_kind(p) == "tests" for p in protocol_uses.get(label, set())),
         }
        for label in PROTOCOL_MARKERS
    ]
    facade = ast.parse((repo / "main_computer/hub.py").read_text(encoding="utf-8"))
    locally_defined = sorted(node.name for node in facade.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    expected_entrypoints = ["serve_hub", "serve_hub_worker"]
    allowed_routes_source = (repo / "main_computer/dev_test_hub_legacy_routes.py").read_text(encoding="utf-8")
    devtest_boundary_present = "inherited_legacy_hub_route" in allowed_routes_source
    devtest_no_worker_rest = all(marker not in allowed_routes_source for marker in (
        '"/api/hub/v1/workers/register"', '"/api/hub/v1/workers/heartbeat"',
        '"/api/hub/v1/workers/poll"', '"/api/hub/v1/workers/results"',
    ))

    # This is an inventory, not a closed-world proof. Dynamic imports, external
    # plugins and callers outside the scanned repository remain unverified.
    return {
        "schema": "main-computer.p54a.legacy-hub-deletion-inventory.v1",
        "ok": not missing and not parse_failures and locally_defined == expected_entrypoints and devtest_boundary_present and devtest_no_worker_rest,
        "scope": "static source only; no Hub requests, no Worker launch, no FDB transaction, no payment or mutation",
        "read_only": True,
        "files_scanned": python_files,
        "parse_failures": parse_failures,
        "facade_locally_defined": locally_defined,
        "facade_is_entrypoint_only": locally_defined == expected_entrypoints,
        "dev_test_route_allowlist_present": devtest_boundary_present,
        "dev_test_legacy_worker_rest_still_blocked": devtest_no_worker_rest,
        "direct_hub_module_imports": sorted(module_uses.get("module_import", set())),
        "imported_historical_symbol_count": len(symbol_imports),
        "important_module_dependencies": [
            {"source": file, "direct_imports": sorted(important_edges.get(file, set()))}
            for file in IMPORTANT_SOURCE_FILES
        ],
        "historical_symbol_callers": symbol_imports,
        "legacy_protocol_source_references": protocols,
        "removal_approved": [],
        "limitations": [
            "Static references and route strings do not prove runtime reachability or safe deletion.",
            "Dynamic imports, external downstream clients, deployments and configured plugins were not exercised.",
            "Existing CLI entry points, explicit Dev-Test route inheritance and protocol tests must be preserved.",
        ],
    }


def main() -> int:
    # Progress goes to stderr so stdout remains machine-readable JSON.
    result = inventory(progress=lambda message: print(message, file=sys.stderr, flush=True))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
