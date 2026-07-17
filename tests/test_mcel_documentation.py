from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCEL_DOC = ROOT / "pretty_docs" / "mcel-system-guide.md"
AUTHORING_DOC = ROOT / "pretty_docs" / "mcel-application-authoring.md"
CODE_STUDIO_DOC = ROOT / "pretty_docs" / "mcel-code-studio-example.md"
CODE_EDITOR_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-code-editor-requirements.md"
GIT_TOOLS_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-git-tools-requirements.md"
CALCULATOR_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-calculator-requirements.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"
README = ROOT / "README.md"


def test_mcel_system_guide_is_registered_in_pretty_docs() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next((item for item in documents if item.get("path") == "mcel-system-guide.md"), None)

    assert entry is not None
    assert entry["title"] == "MCEL System Guide"
    assert entry["kind"] == "markdown"
    assert isinstance(entry["order"], int)


def test_mcel_system_guide_documents_the_required_boundaries() -> None:
    text = MCEL_DOC.read_text(encoding="utf-8")

    required_phrases = [
        "source meaning",
        "runtime machinery",
        "serialization",
        "serialization firewall",
        "subsumption lattice",
        "adoption case",
        "evidence packets",
        "proof obligations",
        "MCEL.buildAdoptionCase(options)",
        "python tools/build_mcel_runtime.py",
        "tests/test_mcel_runtime_package.py",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_mcel_system_guide_documents_each_law_domain() -> None:
    text = MCEL_DOC.read_text(encoding="utf-8")

    law_files = [
        "mcel-component-law.js",
        "mcel-state-law.js",
        "mcel-data-law.js",
        "mcel-form-law.js",
        "mcel-action-law.js",
        "mcel-render-law.js",
        "mcel-a11y-law.js",
        "mcel-performance-law.js",
        "mcel-layout-law.js",
        "mcel-style-law.js",
        "mcel-chrome-law.js",
    ]

    for law_file in law_files:
        assert law_file in text


def test_readme_points_to_mcel_system_guide() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "## MCEL documentation" in readme
    assert "pretty_docs/mcel-system-guide.md" in readme
    assert "adoption-case gate" in readme

def test_mcel_application_authoring_guide_is_registered_and_current() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-application-authoring.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Application Authoring"
    assert entry["kind"] == "markdown"

    text = AUTHORING_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "MainComputerCodeEditorLayout",
        "MainComputerGitToolsLayout",
        "data-mc-layout-*",
        "data-mcel-layout-*",
        "owned center slot",
        "One scroll owner per recursive unit",
        "Adapt to owned capacity, not only the viewport",
        "Preserve live behavior while changing layout",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_code_studio_example_documents_the_live_aider_workbench() -> None:
    text = CODE_STUDIO_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "Aider Control Surface",
        "Aider repository file map",
        "Evidence and History",
        "mcel-owned-track-containment.v1",
        "code-editor-layout-contract.js",
        "MainComputerCodeEditorLayout",
        "Raw `left`, `top`, `width`, and `height` coordinates are rejected",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_readme_points_to_application_authoring_docs() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "pretty_docs/mcel-application-authoring.md" in readme
    assert "pretty_docs/mcel-code-studio-example.md" in readme
    assert "pretty_docs/mcel-git-tools-requirements.md" in readme
    assert "pretty_docs/mcel-calculator-requirements.md" in readme



def _mcel_doc_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"```(mcel-[a-z-]+)\n(.*?)\n```", re.DOTALL)
    return [(match.group(1), match.group(2)) for match in pattern.finditer(text)]


def _mcel_doc_field(block: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def test_code_editor_requirements_are_registered_and_machine_readable() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-code-editor-requirements.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Code Editor Requirements"
    assert entry["kind"] == "markdown"

    text = CODE_EDITOR_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first requirements contract",
        "current: structural MWSL workbench + domain-enrichment behavior",
        "planned: executable Code Editor semantic adapter",
        "code-editor.source.canonical",
        "code-editor.mutation.explicit-boundaries",
        "code-editor.aider.plan-before-apply",
        "code-editor.adapter.executable-semantics",
        "code-editor.intent.save-file",
        "code-editor.intent.run-code",
        "prohibited-until-execution-adapter",
        "MCEL truth gate does not report fullApplicationSemanticReady",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    assert len(blocks) >= 20

    ids: list[str] = []
    for block_type, block in blocks:
        block_id = _mcel_doc_field(block, "id")
        app = _mcel_doc_field(block, "app")
        assert block_id, f"{block_type} block is missing id"
        if block_type != "mcel-app":
            assert app == "code-editor", f"{block_id} should be scoped to code-editor"
        ids.append(block_id)

    assert len(ids) == len(set(ids))


def test_git_tools_requirements_are_registered_and_machine_readable() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-git-tools-requirements.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Git Tools Requirements"
    assert entry["kind"] == "markdown"

    text = GIT_TOOLS_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first requirements contract",
        "current: scope-limited semantic runtime for governed publishing",
        "planned: full Git Tools semantic runtime",
        "governed-publish-partial",
        "git-tools.repository.evidence-first",
        "git-tools.push.governed",
        "git-tools.project-card.primary-publishing",
        "git-tools.remote-sync.explicit",
        "git-tools.adapter.truth-gated-readiness",
        "git-tools.intent.refresh-status",
        "current_adapter_status: executable",
        "git-tools.intent.inspect-working-tree",
        "current_adapter_status: declared-only",
        "git-tools.intent.prepare-push",
        "current_adapter_status: preflight-only",
        "git-tools.intent.run-manual-command",
        "current_adapter_status: prohibited",
        "MCEL truth gate does not report fullApplicationSemanticReady",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    assert len(blocks) >= 30

    ids: list[str] = []
    for block_type, block in blocks:
        block_id = _mcel_doc_field(block, "id")
        app = _mcel_doc_field(block, "app")
        assert block_id, f"{block_type} block is missing id"
        if block_type != "mcel-app":
            assert app == "git-tools", f"{block_id} should be scoped to git-tools"
        ids.append(block_id)

    assert len(ids) == len(set(ids))


def test_calculator_requirements_are_registered_and_machine_readable() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-calculator-requirements.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Calculator Requirements"
    assert entry["kind"] == "markdown"

    text = CALCULATOR_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first requirements contract",
        "current: domain-ready calculator planner + domain pack",
        "planned: full Calculator semantic runtime",
        "Calculator should be the small reference app",
        "Roadmap use case: compare monthly costs",
        "calculator.use-case.compare-monthly-costs",
        "expected_break_even: \"x = 100\"",
        "deterministic calculation remains authoritative",
        "calculator.compute.local-deterministic",
        "calculator.expression.sanitized-parser",
        "calculator.graph.canvas-owned-output",
        "calculator.layout.primary-surface",
        "calculator.intent.evaluate-expression",
        "calculator.intent.draw-graph",
        "calculator.intent.evaluate-mathics",
        "current_adapter_status: not-registered",
        "target_adapter_status: executable",
        "MCEL truth gate should eventually report fullApplicationSemanticReady",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    assert len(blocks) >= 36

    ids: list[str] = []
    for block_type, block in blocks:
        block_id = _mcel_doc_field(block, "id")
        app = _mcel_doc_field(block, "app")
        assert block_id, f"{block_type} block is missing id"
        if block_type != "mcel-app":
            assert app == "calculator", f"{block_id} should be scoped to calculator"
        ids.append(block_id)

    assert len(ids) == len(set(ids))
