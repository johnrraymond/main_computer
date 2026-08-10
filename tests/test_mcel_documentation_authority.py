from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_DOC = ROOT / "pretty_docs" / "mcel-status-and-roadmap.md"
PRETTY_DOCS_INDEX = ROOT / "pretty_docs" / "index.json"
README = ROOT / "README.md"
TODO = ROOT / "TODO.md"
CODE_EDITOR_REQUIREMENTS = ROOT / "pretty_docs" / "mcel-code-editor-requirements.md"
DSL_AUTHORING_SURFACE = ROOT / "pretty_docs" / "mcel-dsl-app-authoring-surface.md"
CANONICAL_APP_AUTHORING = ROOT / "pretty_docs" / "mcel-canonical-app-authoring.md"
APPLICATION_AUTHORING = ROOT / "pretty_docs" / "mcel-application-authoring.md"
APPLICATION_SCAFFOLDING = ROOT / "pretty_docs" / "mcel-application-scaffolding.md"
APP_SURFACE_REGISTRY = ROOT / "pretty_docs" / "mcel-app-surface-registry.md"
GIT_TOOLS_REQUIREMENTS = ROOT / "pretty_docs" / "mcel-git-tools-requirements.md"
CODE_EDITOR_ADAPTER_TESTS = ROOT / "tests" / "test_mcel_code_editor_semantic_adapter.py"
GIT_TOOLS_ADAPTER_TESTS = ROOT / "tests" / "test_mcel_git_tools_semantic_adapter.py"

AUTHORIZED_NEXT_HEADING = re.compile(
    r"^## Authorized next code candidate\s*$",
    flags=re.MULTILINE,
)

STALE_PLANNING_PHRASES = (
    "next target: compare the requirements payload",
    "the next phase after the registry is to connect it to mcel lab",
    "requirements are not yet automatically compared",
    "mcel lab can later parse",
    "future semantic adapter",
    "connect the requirements payload to app blueprints",
    "compare the requirements payload to app blueprints",
)


def _authority_corpus() -> dict[Path, str]:
    paths = [
        README,
        TODO,
        *sorted((ROOT / "pretty_docs").glob("mcel-*.md")),
    ]
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _finding_block(path: Path, finding_id: str) -> str:
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(
        r"```mcel-finding\s*\n(?P<body>.*?)\n```",
        text,
        flags=re.DOTALL,
    ):
        body = match.group("body")
        if re.search(
            rf"^id:\s*{re.escape(finding_id)}\s*$",
            body,
            flags=re.MULTILINE,
        ):
            return body
    raise AssertionError(f"Missing mcel-finding block: {finding_id}")


def _test_function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            assert node.end_lineno is not None
            return "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"Missing proof test {function_name} in {path.relative_to(ROOT)}")


def test_status_document_is_indexed_and_linked_from_repository_entry_points() -> None:
    index = json.loads(PRETTY_DOCS_INDEX.read_text(encoding="utf-8"))
    documents = index.get("documents", [])
    entries = [
        item
        for item in documents
        if item.get("path") == "mcel-status-and-roadmap.md"
    ]

    assert entries == [
        {
            "path": "mcel-status-and-roadmap.md",
            "title": "MCEL Status and Roadmap",
            "kind": "markdown",
            "order": 34,
        }
    ]

    canonical_path = "pretty_docs/mcel-status-and-roadmap.md"
    assert canonical_path in README.read_text(encoding="utf-8")
    assert canonical_path in TODO.read_text(encoding="utf-8")


def test_status_document_is_the_only_authority_for_upcoming_mcel_code_work() -> None:
    corpus = _authority_corpus()
    authority_documents = [
        path.relative_to(ROOT).as_posix()
        for path, text in corpus.items()
        if AUTHORIZED_NEXT_HEADING.search(text)
    ]

    assert authority_documents == ["pretty_docs/mcel-status-and-roadmap.md"]

    status_text = corpus[STATUS_DOC]
    normalized_status = re.sub(r"\s+", " ", status_text)
    assert len(AUTHORIZED_NEXT_HEADING.findall(status_text)) == 1
    assert "No next MCEL code candidate is authorized in this snapshot." in status_text
    assert (
        "MCEL Lab semantic-form provenance and conformance closure, is complete "
        "as an implemented read-only inspector baseline."
        in normalized_status
    )
    assert "Future code candidates must be separately specified" in normalized_status
    assert "all 40 registered form primitives retain exact" in status_text
    assert "MCEL Lab renders 9 primitive cards in 8 ordered groups" in status_text
    assert "contract-status label and documentation source" in normalized_status
    assert "without claiming implementation proof" in status_text
    assert "per-primitive `observed`, `missing`, or `unknown`" not in status_text
    assert "new semantic-form feature, implementation-status inference system" in normalized_status
    assert "source mutation path, repair applicator" in normalized_status
    assert "active browser explorer, browser mutation workflow" in normalized_status
    assert "no declared application maturity is changed" in normalized_status
    assert "`mcel-evidence-scope-v1`" in status_text
    assert "`--overwrite-canonical`" in status_text
    assert "mcel-browser-observation-producer.js" in status_text
    assert (
        "proves that the locator resolves uniquely to the supplied attached root"
        in normalized_status
    )
    assert "mcel.browser-observation.capture-limits.v1" in status_text
    assert "mcel.redaction-policy.stub.v1" in status_text
    assert "It performs no masking and provides no sensitive-data protection." in normalized_status
    assert "The producer continues to emit no verifying claims." in normalized_status
    assert (
        "Layout, visual, source, transition, ridge, and general live-browser collection "
        "by the observation producer remain deferred."
        in normalized_status
    )


def test_todo_treats_existing_application_surfaces_as_implemented_baselines() -> None:
    text = TODO.read_text(encoding="utf-8")
    folded = text.casefold()

    stale_greenfield_phrases = (
        "create a built-in code editor interface similar to vs code:",
        "add an applications area to the frontend:",
        "add a task manager/top-style app",
        "add a terminal app",
        "add a spreadsheet application",
    )
    for phrase in stale_greenfield_phrases:
        assert phrase not in folded

    assert "Continue the existing built-in Code Editor" in text
    assert "Continue the existing Applications area" in text
    assert (
        "implemented baselines whose remaining work is hardening, verification, "
        "or bounded feature expansion"
        in text
    )
    for surface_name in (
        "Code Editor",
        "Terminal",
        "Task Manager",
        "Spreadsheet",
        "Git Tools",
        "File Explorer",
        "Website Builder",
        "MCEL Lab",
    ):
        assert surface_name in text


def test_stale_mcel_planning_language_does_not_return() -> None:
    corpus = "\n".join(_authority_corpus().values()).casefold()

    for stale_phrase in STALE_PLANNING_PHRASES:
        assert stale_phrase not in corpus



def test_app_authoring_docs_make_static_surface_bundle_a_no_backfill_gate() -> None:
    dsl_authoring = DSL_AUTHORING_SURFACE.read_text(encoding="utf-8")
    canonical_authoring = CANONICAL_APP_AUTHORING.read_text(encoding="utf-8")
    application_authoring = APPLICATION_AUTHORING.read_text(encoding="utf-8")
    scaffolding = APPLICATION_SCAFFOLDING.read_text(encoding="utf-8")
    registry = APP_SURFACE_REGISTRY.read_text(encoding="utf-8")

    assert "A semantic-runtime app must not rely on a later central backfill" in dsl_authoring
    assert "app.presentation.semanticSurface" in dsl_authoring
    assert "app.layout.grammar" in dsl_authoring
    assert "`mcel.application-surface-bundle.v1`" in dsl_authoring
    assert "contracts/surface-bundle.json" in dsl_authoring
    assert "only then may the surface registry require those layers" in dsl_authoring
    assert (
        "package-local tests prove surface bundle declarations, selector grounding, "
        "intent mapping, primary surface, default-hidden regions, and layout constraints"
        in dsl_authoring
    )

    assert "Use `mcel_apps/code-editor/application.js`" in canonical_authoring
    assert "Code Editor's DSL surface declarations" in canonical_authoring
    assert "host-bound static semanticSurface/layoutGrammar bundle declaration example" in canonical_authoring

    normalized_application_authoring = re.sub(r"\s+", " ", application_authoring)
    assert "not optional follow-up work" in normalized_application_authoring
    assert "must come from the app source" in normalized_application_authoring

    assert "Static surface bundle requirement" in scaffolding
    assert "This is a no-backfill invariant." in scaffolding
    assert (
        "generated DSL source includes semanticSurface and layoutGrammar declarations "
        "before semantic-runtime registry promotion"
        in scaffolding
    )

    normalized_registry = re.sub(r"\s+", " ", registry)
    assert "Enrollment rule for new or ported apps" in registry
    assert "A registry promotion must follow source truth." in registry
    assert "prevents the Code Editor-style late backfill from becoming normal process" in normalized_registry


def test_deprecated_findings_remain_linked_to_executable_adapter_proof_tests() -> None:
    proof_contracts = (
        {
            "finding_id": "code-editor.finding.docs-to-implementation-gap",
            "requirements_doc": CODE_EDITOR_REQUIREMENTS,
            "proof_test_path": CODE_EDITOR_ADAPTER_TESTS,
            "proof_test_name": "test_code_editor_retired_adapter_exports_only_a_legacy_marker",
            "documented_checks": (
                "tests/test_mcel_code_editor_semantic_adapter.py",
                "tests/test_mcel_app_surface_registry.py",
                "tests/test_mcel_code_editor_browser_smoke.py",
            ),
            "proof_assertions": (
                'assert payload == {',
                '"retired": True',
                '"authority": "MainComputerCodeEditorRuntime"',
                '"runtimeFacade": "MainComputerCodeEditorRuntime"',
            ),
        },
        {
            "finding_id": "git-tools.finding.declared-only-read-intents",
            "requirements_doc": GIT_TOOLS_REQUIREMENTS,
            "proof_test_path": GIT_TOOLS_ADAPTER_TESTS,
            "proof_test_name": (
                "test_git_tools_gap_closure_executes_read_inspection_and_"
                "prepare_push_without_full_promotion"
            ),
            "documented_checks": (
                "tests/test_mcel_git_tools_semantic_adapter.py",
                "tests/test_mcel_domain_adapter_registry.py",
            ),
            "proof_assertions": (
                'adapter.executeIntent("inspectWorkingTree"',
                'adapter.executeIntent("inspectRemotes"',
                'adapter.executeIntent("inspectPatchInventory"',
                'assert readiness["declaredOnlyIntentCount"] == 0',
            ),
        },
        {
            "finding_id": "git-tools.finding.prepare-push-gap",
            "requirements_doc": GIT_TOOLS_REQUIREMENTS,
            "proof_test_path": GIT_TOOLS_ADAPTER_TESTS,
            "proof_test_name": (
                "test_git_tools_gap_closure_executes_read_inspection_and_"
                "prepare_push_without_full_promotion"
            ),
            "documented_checks": (
                "tests/test_mcel_git_tools_semantic_adapter.py",
                "tests/test_mcel_git_tools_semantic_panel.py",
            ),
            "proof_assertions": (
                'adapter.executeIntent("preparePush"',
                'assert result["preparePush"]["executionAttempted"] is False',
                'assert result["preparePush"]["executionBinding"] == "mcel-preflight"',
                'assert result["preparePush"]["receipt"]["kind"] == "preflight-decision-receipt"',
            ),
        },
    )

    for contract in proof_contracts:
        finding_body = _finding_block(
            contract["requirements_doc"],
            contract["finding_id"],
        )
        assert re.search(
            r"^status:\s*deprecated\s*$",
            finding_body,
            flags=re.MULTILINE,
        )
        assert contract["proof_test_path"].is_file()
        for documented_check in contract["documented_checks"]:
            assert documented_check in finding_body
            assert (ROOT / documented_check).is_file()

        proof_source = _test_function_source(
            contract["proof_test_path"],
            contract["proof_test_name"],
        )
        for proof_assertion in contract["proof_assertions"]:
            assert proof_assertion in proof_source
