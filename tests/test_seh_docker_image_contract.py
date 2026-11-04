"""The image must contain every local SEH import and its healthcheck tools.

A locally passing relay import is not proof the minimal Docker image contains
that module.  These tests exercise the image's declared file/dependency contract
without invoking Docker or contacting Coolify.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tools.seh_control import git_deployment

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/seh/Dockerfile"


def _copy_sources(dockerfile: str) -> set[str]:
    # All SEH COPY commands use the two-operand form, with no wildcards.
    return {
        parts[1]
        for line in dockerfile.splitlines()
        if (parts := line.strip().split()) and parts[0] == "COPY" and len(parts) == 3
    }


def test_relay_dockerfile_copies_all_local_runtime_modules():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    sources = _copy_sources(dockerfile)
    relay = ast.parse((ROOT / "main_computer/seh/relay.py").read_text(encoding="utf-8"))
    required = {
        "main_computer/seh/__init__.py",
        "main_computer/seh/relay.py",
        "main_computer/slh/authenticator.py",
    }
    for node in ast.walk(relay):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            required.add(f"main_computer/seh/{node.module.replace('.', '/')}.py")
    assert required <= sources, f"Modules absent from image: {sorted(required - sources)}"
    assert required <= set(git_deployment.SOURCES), "Git publication must also track image inputs"
    assert all((ROOT / path).is_file() for path in required)
    assert "PYTHONPATH=/opt/seh" in dockerfile
    assert 'CMD ["python", "-m", "main_computer.seh.relay"]' in dockerfile


def test_coolify_wget_healthcheck_binary_is_installed():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    # Coolify's application healthcheck invokes wget even though the Dockerfile
    # has a separate Python HEALTHCHECK; both commands must be executable.
    install = re.search(r"apt-get install\s+-y\s+--no-install-recommends\s+([^\n]+)", dockerfile)
    assert install is not None
    assert "wget" in install.group(1).split()
    assert "HEALTHCHECK" in dockerfile and "/healthz" in dockerfile
