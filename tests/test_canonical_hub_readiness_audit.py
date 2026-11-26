"""Active Hub consumers must use canonical health/identity contracts."""
from pathlib import Path
import os
import re

ROOT = Path(__file__).resolve().parents[1]
RETIRED = re.compile(r"/api/hub/(?:v1/)?(?:status|health)(?![a-zA-Z0-9_-])")
# These are not live Hub readiness consumers. The Temporal suites and phase
# smokes fail before network I/O; the chaos helpers belong to the retired suite.
EXCLUDED = {
    "deploy/local-platform/site-server/app.py",  # distinct website emulator
    "tools/local-platform/diagnose-local-platform.py",  # same emulator
    "tools/dev_test_hub_inheritance_verify.py",  # negative route contract
    "main_computer/node_behavior_chaos.py",
    "main_computer/temporal_fdb_hub_multi_hub_smoke.py",
    "main_computer/temporal_fdb_hub_node_market_smoke.py",
    "main_computer/temporal_fdb_hub_stress_smoke.py",
    "scripts/run_phase9_market_backed_paid_ai_request_smoke.py",
    "scripts/run_phase10_deterministic_worker_runtime_contract_smoke.py",
}


SOURCE_ROOTS = ("main_computer", "scripts", "tools", "deploy", "docker", "mcel_apps")
SOURCE_SUFFIXES = {".py", ".ps1", ".js", ".ts", ".json", ".yml", ".yaml", ".sh", ".html", ".bat"}
# Prune before descent, not after visiting every installed dependency file.
# These are generated/local trees, not authoritative Hub consumers.
PRUNED_NAMES = {
    "node_modules", ".venv", "venv", "env", "__pycache__", ".git",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".cache-synpress",
    "build", "dist", ".eggs", "reports", "test-results", "playwright-report",
    "blob-report", "runtime", ".main_computer_browser_profile", ".main-computer",
    ".main_computer", "generated_component_docs", "main_computer_test", ".tmp",
}
PRUNED_PATHS = {
    "deploy/local-platform/generated", "deploy/scheduler-lab/output",
    "tools/patching", "tools/documentation",
    "main_computer/web/applications/vendor",  # bundled third-party dependencies
}


def _is_link(path):
    # Python 3.12 on Windows exposes junctions separately from symlinks.
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _source_paths(root):
    def onerror(error):
        raise error

    for folder in SOURCE_ROOTS:
        start = root / folder
        if not start.is_dir() or _is_link(start):
            raise AssertionError(f"Missing or redirected source root: {start}")
        for directory, dirs, files in os.walk(start, topdown=True, followlinks=False, onerror=onerror):
            parent = Path(directory)
            dirs[:] = sorted(d for d in dirs if (
                d.lower() not in PRUNED_NAMES
                and not d.lower().endswith((".egg-info", " - copy"))
                and (parent / d).relative_to(root).as_posix() not in PRUNED_PATHS
                and not _is_link(parent / d)
            ))
            for filename in sorted(files):
                path = parent / filename
                if path.suffix.lower() in SOURCE_SUFFIXES and not _is_link(path) and path.is_file():
                    yield path


def _retired_route_violations(root):
    violations = []
    for path in _source_paths(root):
        name = path.relative_to(root).as_posix()
        if name not in EXCLUDED and RETIRED.search(path.read_text(encoding="utf-8-sig", errors="replace")):
            violations.append(name)
    return violations


def test_no_retired_health_or_status_in_active_hub_source():
    violations = _retired_route_violations(ROOT)
    assert not violations, violations


def test_census_prunes_generated_trees_but_detects_nested_source(tmp_path):
    for folder in SOURCE_ROOTS:
        (tmp_path / folder).mkdir()
    active = "main_computer/nested/consumer.py"
    ignored = (
        "tools/worker_browser_acceptance/node_modules/pkg/index.js",
        "tools/worker_browser_acceptance/.cache-synpress/probe.js",
        "deploy/local-platform/generated/compose.yml",
        "docker/example/dist/bundle.js",
        "main_computer/web/applications/vendor/library.js",
    )
    for name in (active, *ignored):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('/api/hub/v1/status', encoding="utf-8")
    assert _retired_route_violations(tmp_path) == [active]


def test_retired_phase_smokes_refuse_before_network():
    import ast
    for name in ("scripts/run_phase9_market_backed_paid_ai_request_smoke.py", "scripts/run_phase10_deterministic_worker_runtime_contract_smoke.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        gate = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Raise))
        assert "Retired:" in ast.unparse(main.body[gate])
        assert all(isinstance(n, (ast.Assign, ast.Expr)) for n in main.body[:gate])
        assert not any("get_json(" in ast.unparse(n) or "post_json(" in ast.unparse(n) for n in main.body[:gate])
