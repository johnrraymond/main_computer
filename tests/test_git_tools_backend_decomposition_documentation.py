from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "pretty_docs" / "git-tools-backend-decomposition.md"
REQUIREMENTS = ROOT / "pretty_docs" / "mcel-git-tools-requirements.md"
PUBLISHING = ROOT / "pretty_docs" / "git-tools-project-level-publishing.md"
STATUS = ROOT / "pretty_docs" / "mcel-status-and-roadmap.md"
INDEX = ROOT / "pretty_docs" / "index.json"


def test_git_tools_backend_decomposition_plan_is_indexed_and_cross_linked() -> None:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    entries = [
        item
        for item in index.get("documents", [])
        if item.get("path") == "git-tools-backend-decomposition.md"
    ]
    assert entries == [
        {
            "path": "git-tools-backend-decomposition.md",
            "title": "Git Tools Backend Decomposition Plan",
            "kind": "markdown",
            "order": 65,
        }
    ]

    plan_path = "pretty_docs/git-tools-backend-decomposition.md"
    assert plan_path in REQUIREMENTS.read_text(encoding="utf-8")
    assert plan_path in PUBLISHING.read_text(encoding="utf-8")


def test_git_tools_backend_decomposition_plan_is_non_authorizing() -> None:
    text = PLAN.read_text(encoding="utf-8")

    assert "Planning document only. No implementation is authorized by this document." in text
    assert "pretty_docs/mcel-status-and-roadmap.md" in text
    assert "## Authorized next code candidate" not in text
    assert "```mcel-" not in text

    status_text = STATUS.read_text(encoding="utf-8")
    assert status_text.count("## Authorized next code candidate") == 1


def test_git_tools_backend_decomposition_plan_preserves_compatibility_boundaries() -> None:
    text = PLAN.read_text(encoding="utf-8")

    assert "from main_computer.git_tools import GitToolsService" in text
    assert "/api/applications/git/status" in text
    assert "git-tools-semantic-adapter.js" in text
    assert "git_tools.py` remains the compatibility façade" in text
    assert "No new routes and no frontend changes." in text
    assert "Read-only evidence may run concurrently" in text
    assert "Mutation operations remain serialized per repository" in text
    assert "A slower earlier refresh must not overwrite a newer completed refresh." in text
    assert "Browser shadow comparison" in text
    assert "Compatibility retirement" in text


def test_git_tools_backend_decomposition_plan_requires_reversible_evidence_gates() -> None:
    text = PLAN.read_text(encoding="utf-8")

    assert "Slice 1 — Characterize the compatibility response" in text
    assert "Slice 2 — Extract repository evidence" in text
    assert "Slice 3 — Internally parallelize independent reads" in text
    assert "Slice 4 — Add versioned evidence endpoints" in text
    assert "Slice 5 — Browser shadow comparison" in text
    assert "Slice 6 — Browser cutover" in text
    assert "Slice 7 — Compatibility retirement" in text
    assert text.count("Rollback:") >= 6
    assert "deployed Git Tools runtime evidence" in text
    assert "Git Tools acceptance evidence" in text
    assert "a passing MCEL truth release gate" in text
    assert "no application maturity change unless separately authorized" in text
