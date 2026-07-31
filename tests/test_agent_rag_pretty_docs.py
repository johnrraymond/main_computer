from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PRETTY_DOCS = ROOT / "pretty_docs"

DOCUMENTS = (
    ("agent-rag-overview.md", "Agent and RAG Overview", 51),
    ("agent-rag-operations.md", "Agent and RAG Operations", 52),
    ("rag-retrieval-strategies.md", "RAG Retrieval Strategies", 53),
    ("graphify-retrieval.md", "Graphify Retrieval", 54),
    ("byzantine-rag-ring3.md", "Byzantine RAG and Ring 3", 55),
    ("generated-editor-systems.md", "Generated Editor Systems", 56),
    ("text-console-agent.md", "Text Console Agent", 57),
    ("context-compaction.md", "Context Compaction", 58),
    ("multimodal-rag.md", "Multimodal RAG", 59),
    ("model-boundary.md", "Model Boundary", 60),
    ("agent-artifacts-and-apply.md", "Agent Artifacts and Apply", 61),
    ("agent-runtime-and-recovery.md", "Agent Runtime and Recovery", 62),
    ("agent-rag-evaluation.md", "Agent and RAG Evaluation", 63),
    ("agent-rag-module-map.md", "Agent and RAG Module Map", 64),
)

REQUIRED_SOURCE_PATHS = (
    "main_computer/rag_retriever.py",
    "main_computer/rag_harness.py",
    "main_computer/rag_assisted_thinking_v4.py",
    "main_computer/viewport_routes_rag_assisted_thinking.py",
    "main_computer/chat_ai_subprocess.py",
    "main_computer/website_builder_generated_editor_pipeline.py",
    "main_computer/rag_terminal_result_contract.py",
    "main_computer/rag_terminal_artifact_contract.py",
    "main_computer/rag_code_edit_agent_guidance_smoke.py",
    "main_computer/cli.py",
    "main_computer/providers/ollama.py",
    "main_computer/providers/hub.py",
    "main_computer/viewport_routes_aider.py",
    "main_computer/rag_text_console_control_surface_smoke.py",
    "main_computer/rag_profile_space_latest_png_rag_smoke.py",
    "tests/test_rag_code_edit_agent_guidance_smoke.py",
    "tests/test_data_god_mode_cli.py",
    "tests/test_debug_website_golden_path_no_deterministic_cheats.py",
)


def _index_documents() -> list[dict[str, object]]:
    value = json.loads((PRETTY_DOCS / "index.json").read_text(encoding="utf-8"))
    documents = value.get("documents")
    assert isinstance(documents, list)
    return documents


def _read(name: str) -> str:
    return (PRETTY_DOCS / name).read_text(encoding="utf-8")


def test_agent_rag_documents_exist_and_have_one_h1() -> None:
    for name, title, _order in DOCUMENTS:
        path = PRETTY_DOCS / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        h1_lines = [line for line in text.splitlines() if line.startswith("# ")]
        assert h1_lines == [f"# {title}"]
        assert "[Agent and RAG Overview](agent-rag-overview.md)" in text


def test_agent_rag_documents_are_registered_once_in_contiguous_order() -> None:
    entries = _index_documents()
    by_path: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        path = str(entry.get("path") or "")
        by_path.setdefault(path, []).append(entry)

    for path, title, order in DOCUMENTS:
        assert len(by_path.get(path, [])) == 1
        entry = by_path[path][0]
        assert entry["title"] == title
        assert entry["kind"] == "markdown"
        assert entry["order"] == order

    assert [order for _path, _title, order in DOCUMENTS] == list(range(51, 65))


def test_agent_rag_documents_contain_no_unsafe_control_characters() -> None:
    unsafe = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
    for name, _title, _order in DOCUMENTS:
        text = _read(name)
        assert unsafe.search(text) is None, name
        assert "\r" not in text, name


def test_operation_catalog_exposes_required_high_level_operations() -> None:
    text = _read("agent-rag-operations.md")
    required = (
        "Inspect target",
        "Retrieve evidence",
        "Route retrieval",
        "Expand retrieval",
        "Grade evidence",
        "Disambiguate evidence",
        "Answer question",
        "Diagnose problem",
        "Plan change",
        "Propose change",
        "Build artifact",
        "Dry-run artifact",
        "Apply approved change",
        "Verify change",
        "Run autonomous task",
        "Run consensus decision",
        "Compact consensus evidence",
        "Build graph",
        "Hydrate graph evidence",
        "Repair model output",
        "Resume run",
        "Evaluate retrieval",
        "Run contract smokes",
    )
    for phrase in required:
        assert phrase in text


def test_graphify_document_records_retirement_and_hydration_boundaries() -> None:
    text = _read("graphify-retrieval.md")
    assert "retired experimental evaluation record" in text
    assert "Graphify is not mounted" in text
    assert "deterministic retrieval baseline remains the Website Builder default" in text
    assert "No Graphify smoke or A/B scripts are retained in this snapshot" in text
    assert "exact-source hydration" in text
    assert "Graphify is a retriever" in text
    assert "Never let Graphify apply a patch" in text


def test_agent_rag_documents_do_not_reference_deleted_graphify_scripts() -> None:
    for name, _title, _order in DOCUMENTS:
        assert "scripts/graphify_" not in _read(name), name


def test_byzantine_document_preserves_host_mutation_authority() -> None:
    text = _read("byzantine-rag-ring3.md")
    assert "Every worker, reviewer, verifier, merge result, and Hub response is untrusted" in text
    assert "Consensus is not apply authority" in text
    assert "deterministic host selection or rejection" in text
    assert "main-computer data" in text
    assert "--god-mode" in text


def test_artifact_document_preserves_delete_and_fuzz_semantics() -> None:
    text = _read("agent-artifacts-and-apply.md")
    assert "does **not** infer deletion from omitted files" in text
    assert "--allowfuzz" in text
    assert "Do not enable `--allowfuzz` automatically" in text
    assert "python new_patch.py replacement-files.zip --dry-run" in text


def test_evaluation_document_protects_live_website_state() -> None:
    text = _read("agent-rag-evaluation.md")
    for path in (
        "runtime/websites",
        "runtime/local-platform/sites.json",
        "deploy/local-platform/generated/docker-compose.websites.yml",
    ):
        assert path in text
    assert "external `debug-*` fixtures" in text


def test_module_map_references_existing_source_and_test_files() -> None:
    for relative in REQUIRED_SOURCE_PATHS:
        assert (ROOT / relative).is_file(), relative


def test_every_document_has_provenance_and_status_language() -> None:
    allowed_statuses = (
        "mounted",
        "shared",
        "operator",
        "contract smoke",
        "experimental",
        "compatibility",
        "historical",
        "source-inspected",
    )
    for name, _title, _order in DOCUMENTS:
        text = _read(name)
        assert re.search(
            r"Source snapshot: `main_computer_test-\d{8}-\d{6}\.zip`",
            text,
        ), name
        assert "Model-backed verification: not run" in text
        lowered = text.lower()
        assert any(status in lowered for status in allowed_statuses), name
