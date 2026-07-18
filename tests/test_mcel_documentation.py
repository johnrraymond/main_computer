from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCEL_DOC = ROOT / "pretty_docs" / "mcel-system-guide.md"
AUTHORING_DOC = ROOT / "pretty_docs" / "mcel-application-authoring.md"
REQUIREMENTS_LANGUAGE_DOC = ROOT / "pretty_docs" / "mcel-requirements-language.md"
CODE_STUDIO_DOC = ROOT / "pretty_docs" / "mcel-code-studio-example.md"
CODE_EDITOR_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-code-editor-requirements.md"
GIT_TOOLS_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-git-tools-requirements.md"
CALCULATOR_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-calculator-requirements.md"
FILE_EXPLORER_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-file-explorer-requirements.md"
WEBSITE_BUILDER_REQUIREMENTS_DOC = ROOT / "pretty_docs" / "mcel-website-builder-requirements.md"
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
    assert "pretty_docs/mcel-requirements-language.md" in readme
    assert "pretty_docs/mcel-git-tools-requirements.md" in readme
    assert "pretty_docs/mcel-calculator-requirements.md" in readme
    assert "pretty_docs/mcel-file-explorer-requirements.md" in readme
    assert "pretty_docs/mcel-website-builder-requirements.md" in readme



def _mcel_doc_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"```(mcel-[a-z-]+)\n(.*?)\n```", re.DOTALL)
    return [(match.group(1), match.group(2)) for match in pattern.finditer(text)]


def _mcel_doc_field(block: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None



def test_mcel_requirements_language_is_registered_and_defines_expanded_grammar() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-requirements-language.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Requirements Language"
    assert entry["kind"] == "markdown"

    text = REQUIREMENTS_LANGUAGE_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first grammar",
        "BCP 14 requirement levels",
        "Gherkin-style acceptance thinking",
        "Schema-style validation",
        "Operation-style intent definitions",
        "Responsibility-based architecture",
        "mcel-app",
        "mcel-use-case",
        "mcel-object",
        "mcel-region",
        "mcel-requirement",
        "mcel-intent",
        "mcel-acceptance",
        "mcel-finding",
        "mcel-evidence",
        "mcel-receipt",
        "mcel-boundary",
        "mcel-risk",
        "mcel-adapter",
        "mcel-layout-pattern",
        "mcel-source-binding",
        "mcel-test-binding",
        "mcel-runtime-check",
        "Runtime-observable contract checks",
        "overview",
        "objects",
        "workflows",
        "layout",
        "actions",
        "capabilities",
        "evidence",
        "source",
        "tests",
        "annotations",
        "findings",
        "repair",
        "identity",
        "navigation",
        "primary",
        "inspector",
        "status",
        "advanced",
        "save is not publish",
        "preview is not commit",
        "commit is not push",
        "explain is not calculate",
        "browse is not modify",
        "inspect is not execute",
        "Parser requirements",
        "Truth-gate rule",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    grammar_blocks = [block for block_type, block in blocks if block_type == "mcel-grammar"]
    assert len(grammar_blocks) >= 17

    ids: list[str] = []
    for block in grammar_blocks:
        block_id = _mcel_doc_field(block, "id")
        status = _mcel_doc_field(block, "status")
        block_name = _mcel_doc_field(block, "block")
        purpose = _mcel_doc_field(block, "purpose")
        assert block_id, "mcel-grammar block is missing id"
        assert status == "specified"
        assert block_name and block_name.startswith("mcel-")
        assert purpose
        assert "required_fields:" in block
        ids.append(block_id)

    assert len(ids) == len(set(ids))


def test_mcel_requirements_language_covers_existing_requirement_docs() -> None:
    grammar_text = REQUIREMENTS_LANGUAGE_DOC.read_text(encoding="utf-8")
    requirement_docs = [
        CODE_EDITOR_REQUIREMENTS_DOC,
        GIT_TOOLS_REQUIREMENTS_DOC,
        CALCULATOR_REQUIREMENTS_DOC,
        FILE_EXPLORER_REQUIREMENTS_DOC,
        WEBSITE_BUILDER_REQUIREMENTS_DOC,
    ]

    all_ids: list[str] = []
    block_types: set[str] = set()
    statuses: set[str] = set()
    for doc in requirement_docs:
        text = doc.read_text(encoding="utf-8")
        blocks = _mcel_doc_blocks(text)
        assert blocks, f"{doc.name} should contain mcel-* blocks"
        for block_type, block in blocks:
            block_types.add(block_type)
            block_id = _mcel_doc_field(block, "id")
            assert block_id, f"{doc.name} {block_type} block is missing id"
            all_ids.append(block_id)
            status = _mcel_doc_field(block, "status")
            if status:
                statuses.add(status)
            if block_type != "mcel-app":
                assert _mcel_doc_field(block, "app"), f"{block_id} should be app-scoped"
            if block_type == "mcel-intent":
                assert _mcel_doc_field(block, "risk"), f"{block_id} should declare risk"

    assert len(all_ids) == len(set(all_ids))

    for block_type in block_types:
        assert block_type in grammar_text, f"{block_type} should be covered by the grammar"

    for status in statuses:
        assert f"`{status}`" in grammar_text, f"{status} should be an allowed status"

    required_risk_words = [
        "read-only",
        "local-state",
        "local-file-mutation",
        "local-repository-mutation",
        "remote-mutation",
        "execution",
        "security-sensitive",
        "prohibited",
    ]
    for risk in required_risk_words:
        assert f"`{risk}`" in grammar_text


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
        "Runtime-observable diagnosis contract",
        "code-editor.runtime-check.authoring-primary-monaco",
        "code-editor.contract.authoring.monaco-golden-path",
        "code-editor.binding.authoring-monaco-surface",
        "code-editor.test.authoring-monaco-diagnosis",
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


def test_file_explorer_requirements_are_registered_and_machine_readable() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-file-explorer-requirements.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL File Explorer Requirements"
    assert entry["kind"] == "markdown"

    text = FILE_EXPLORER_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first requirements contract",
        "current: domain-ready read-only File Explorer planner + domain pack",
        "planned: full File Explorer semantic runtime",
        "navigation + list + preview",
        "Roadmap use case: inspect a project file safely",
        "file-explorer.use-case.inspect-project-file-safely",
        "Roadmap use case: browse a mounted Windows drive",
        "file-explorer.use-case.browse-mounted-windows-drive",
        "File Explorer may list, search, classify, and preview files inside an approved root",
        "file-explorer.read-only.core-law",
        "file-explorer.root-boundary.enforced",
        "file-explorer.preview.bounded",
        "file-explorer.search.bounded",
        "file-explorer.classification.visible",
        "file-explorer.handoff.explicit",
        "file-explorer.layout.directory-list",
        "file-explorer.intent.inspect-roots",
        "file-explorer.intent.preview-entry",
        "file-explorer.intent.delete-file",
        "current_adapter_status: not-registered",
        "target_adapter_status: executable",
        "MCEL truth gate reports File Explorer fullApplicationSemanticReady",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    assert len(blocks) >= 32

    ids: list[str] = []
    for block_type, block in blocks:
        block_id = _mcel_doc_field(block, "id")
        app = _mcel_doc_field(block, "app")
        assert block_id, f"{block_type} block is missing id"
        if block_type != "mcel-app":
            assert app == "file-explorer", f"{block_id} should be scoped to file-explorer"
        ids.append(block_id)

    assert len(ids) == len(set(ids))


def test_website_builder_requirements_are_registered_and_machine_readable() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entry = next(
        (item for item in documents if item.get("path") == "mcel-website-builder-requirements.md"),
        None,
    )
    assert entry is not None
    assert entry["title"] == "MCEL Website Builder Requirements"
    assert entry["kind"] == "markdown"

    text = WEBSITE_BUILDER_REQUIREMENTS_DOC.read_text(encoding="utf-8")
    required_phrases = [
        "documentation-first requirements contract",
        "current: working Website Builder + saved website project manifests + local/dev/remote publish lanes",
        "planned: full Website Builder semantic runtime",
        "Website Builder owns site editing, runtime setup, preview, and publish planning",
        "Git Tools owns repository add/commit/push evidence",
        "website-builder.use-case.edit-preview-saved-site",
        "website-builder.use-case.configure-blog-runtime",
        "website-builder.use-case.publish-selected-lane",
        "website-builder.use-case.git-tools-handoff",
        "website-builder.source.project-folder-canonical",
        "website-builder.save.no-publish",
        "website-builder.git.delegated-to-git-tools",
        "website-builder.region.preview-surface",
        "website-builder.region.publish-actions",
        "website-builder.intent.save-site",
        "website-builder.intent.preview-draft",
        "website-builder.intent.publish-local-server",
        "website-builder.intent.prepare-git-handoff",
        "current_adapter_status: not-registered",
        "target_adapter_status: executable",
    ]
    for phrase in required_phrases:
        assert phrase in text

    blocks = _mcel_doc_blocks(text)
    assert len(blocks) >= 40

    ids: list[str] = []
    for block_type, block in blocks:
        block_id = _mcel_doc_field(block, "id")
        app = _mcel_doc_field(block, "app")
        assert block_id, f"{block_type} block is missing id"
        if block_type != "mcel-app":
            assert app == "website-builder", f"{block_id} should be scoped to website-builder"
        ids.append(block_id)

    assert len(ids) == len(set(ids))

