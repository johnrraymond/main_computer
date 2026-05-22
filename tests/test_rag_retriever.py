from __future__ import annotations

import json
from pathlib import Path

from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig, load_upload_contexts


def test_deterministic_retriever_selects_path_and_content_matches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main_computer").mkdir()
    (repo / "main_computer" / "docker_executor.py").write_text(
        "class DockerExecutor:\n"
        "    def run(self):\n"
        "        return 'executor route artifact'\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Readme\nDocker executor notes\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00\x01")
    (repo / "generated_component_docs").mkdir()
    (repo / "generated_component_docs" / "noise.py").write_text("executor executor executor\n", encoding="utf-8")

    retriever = DeterministicRagRetriever(RagRetrieverConfig(repo_dir=repo, max_context_chars=5000))
    result = retriever.retrieve(["docker executor route"])

    paths = [candidate.path for candidate in result.candidates]
    assert "main_computer/docker_executor.py" in paths
    assert "README.md" in paths
    assert "generated_component_docs/noise.py" not in paths
    assert result.chunks
    assert result.used_chars <= result.context_budget_chars
    assert any("DockerExecutor" in chunk.content for chunk in result.chunks)


def test_retriever_inventory_loads_upload_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    executor_root = repo / "runtime" / "executor"
    upload_dir = executor_root / "inputs" / "upload_0123456789abcdef"
    upload_dir.mkdir(parents=True)
    (upload_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": "upload_0123456789abcdef",
                "filename": "data.csv",
                "mime_type": "text/csv",
                "size": 12,
                "container_path": "/inputs/upload_0123456789abcdef/payload.bin",
                "host_path": str(upload_dir / "payload.bin"),
            }
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")

    contexts = load_upload_contexts(["upload_0123456789abcdef"], executor_root=executor_root, repo_dir=repo)
    assert contexts[0].filename == "data.csv"
    assert contexts[0].container_path == "/inputs/upload_0123456789abcdef/payload.bin"

    retriever = DeterministicRagRetriever(RagRetrieverConfig(repo_dir=repo))
    inventory = retriever.inventory(upload_ids=["upload_0123456789abcdef"], executor_root=executor_root)
    assert inventory["uploads"][0]["filename"] == "data.csv"
