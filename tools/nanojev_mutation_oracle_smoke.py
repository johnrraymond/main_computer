#!/usr/bin/env python3
"""Read-only repository smoke for mutation-oracle feasibility.

The smoke creates a disposable Python fixture outside the repository, then proves:
1. the baseline tests pass;
2. a source-text mutation with an identical Python AST still passes;
3. a parse-valid mutation with a different AST is killed by the same tests;
4. the real repository sentinel file is unchanged.

Nothing in the target repository is edited.  All generated source and pytest state lives
inside tempfile.TemporaryDirectory() and is deleted when the smoke exits.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ast_signature(source: str) -> str:
    return ast.dump(ast.parse(source), annotate_fields=True, include_attributes=False)


def run_pytest(workspace: Path, label: str) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    started = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=workspace,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    result = {
        "label": label,
        "returncode": int(started.returncode),
        "passed": started.returncode == 0,
        "output_tail": "\n".join(started.stdout.splitlines()[-8:]),
    }
    emit("pytest_result", **result)
    return result


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sentinel = repo_root / "tools" / "nanojev_code_mutation_train.py"
    if not sentinel.is_file():
        raise RuntimeError(f"expected repository sentinel is missing: {sentinel}")
    sentinel_before = sha256_file(sentinel)

    original = '''\
def allowed(x: int) -> bool:\n    label = "limit"\n    assert label == "limit"\n    return x < 10\n'''
    # Surface-only edits: quote style and redundant parentheses.  Python AST must remain identical.
    preserved = '''\
def allowed(x: int) -> bool:\n    label = 'limit'\n    assert label == 'limit'\n    return (x) < 10\n'''
    # Boundary mutation: same valid program shape, but x == 10 now changes result.
    changed = '''\
def allowed(x: int) -> bool:\n    label = "limit"\n    assert label == "limit"\n    return x <= 10\n'''
    tests = '''\
from subject import allowed\n\n\ndef test_below_boundary_is_allowed():\n    assert allowed(9) is True\n\n\ndef test_boundary_is_rejected():\n    assert allowed(10) is False\n\n\ndef test_above_boundary_is_rejected():\n    assert allowed(11) is False\n'''

    original_sig = ast_signature(original)
    preserved_sig = ast_signature(preserved)
    changed_sig = ast_signature(changed)
    if original_sig != preserved_sig:
        raise RuntimeError("preserving fixture does not have an identical AST")
    if original_sig == changed_sig:
        raise RuntimeError("changing fixture unexpectedly has the original AST")
    emit(
        "mutation_fixture_verified",
        preserving_ast_identical=True,
        changing_ast_different=True,
    )

    with tempfile.TemporaryDirectory(prefix="nanojev-mutation-oracle-smoke-") as raw:
        workspace = Path(raw)
        subject = workspace / "subject.py"
        test_file = workspace / "test_subject.py"
        subject.write_text(original, encoding="utf-8")
        test_file.write_text(tests, encoding="utf-8")

        baseline = run_pytest(workspace, "baseline_original")
        if not baseline["passed"]:
            raise RuntimeError("baseline fixture tests do not pass")

        subject.write_text(preserved, encoding="utf-8")
        preserving = run_pytest(workspace, "preserving_mutant")
        if not preserving["passed"]:
            raise RuntimeError("AST-identical preserving mutant changed tested behavior")

        subject.write_text(changed, encoding="utf-8")
        changing = run_pytest(workspace, "changing_mutant")
        if changing["passed"]:
            raise RuntimeError("behavior-changing mutant survived the tests")

        witness = {
            "input": 10,
            "original_expected": False,
            "mutant_behavior": True,
            "test": "test_boundary_is_rejected",
        }
        emit("divergence_witness", **witness)

    sentinel_after = sha256_file(sentinel)
    repo_unchanged = sentinel_before == sentinel_after
    if not repo_unchanged:
        raise RuntimeError("repository sentinel changed during smoke")

    emit(
        "mutation_oracle_smoke_passed",
        baseline_passed=True,
        preserving_ast_identical=True,
        preserving_tests_passed=True,
        changing_ast_different=True,
        changing_mutant_killed=True,
        repository_sentinel_unchanged=True,
        disposable_workspace_deleted=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
