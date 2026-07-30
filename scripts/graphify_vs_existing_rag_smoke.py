#!/usr/bin/env python3
"""A/B smoke benchmark: existing grep-style RAG versus Graphify traversal.

This script does not build a graph and does not call an LLM.  It compares the
repository's current no-model RAG harness with a Claude-style Graphify workflow:

    graphify query QUESTION
    graphify explain ANCHOR
    graphify path SOURCE TARGET

The benchmark questions are about the repository's own RAG, activity, smoke,
and RAG-assisted-thinking code.  The same questions and gold evidence are used
for both lanes.

Recommended invocation from the repository root:

    python scripts/graphify_vs_existing_rag_smoke.py ^
      --repo . ^
      --graph graphify-repo-graph.json ^
      --json-out graphify-vs-rag-report.json ^
      --markdown-out graphify-vs-rag-report.md

PowerShell uses backticks instead of carets for line continuation.
"""

from __future__ import annotations

import argparse
import collections
from dataclasses import asdict, dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence


class SmokeFailure(RuntimeError):
    """A deterministic benchmark setup or execution failure."""


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    question: str
    expected_paths: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    explain_node: str
    explain_source_path: str
    path_source: str
    path_source_path: str
    path_target: str
    path_target_path: str


CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(
        id="harness_pipeline",
        question=(
            "Trace how run_rag_harness uses DeterministicRagRetriever to turn "
            "retrieval queries into context chunks and a grounded final plan."
        ),
        expected_paths=(
            "main_computer/rag_harness.py",
            "main_computer/rag_retriever.py",
            "main_computer/thinking_models.py",
        ),
        expected_symbols=(
            "run_rag_harness",
            "DeterministicRagRetriever",
            "RagRetrievalResult",
        ),
        explain_node="run_rag_harness",
        explain_source_path="main_computer/rag_harness.py",
        path_source="run_rag_harness",
        path_source_path="main_computer/rag_harness.py",
        path_target="DeterministicRagRetriever",
        path_target_path="main_computer/rag_retriever.py",
    ),
    BenchmarkCase(
        id="activity_flow",
        question=(
            "Trace how run_rag_harness emits RAG activity through "
            "RagActivityEmitter and how "
            "test_rag_harness_emits_activity_monitor_events verifies it."
        ),
        expected_paths=(
            "main_computer/rag_harness.py",
            "main_computer/rag_activity.py",
            "main_computer/activity.py",
            "tests/test_rag_harness.py",
        ),
        expected_symbols=(
            "run_rag_harness",
            "RagActivityEmitter",
            "ActivityBus",
            "test_rag_harness_emits_activity_monitor_events",
        ),
        explain_node="RagActivityEmitter",
        explain_source_path="main_computer/rag_activity.py",
        path_source="run_rag_harness",
        path_source_path="main_computer/rag_harness.py",
        path_target="RagActivityEmitter",
        path_target_path="main_computer/rag_activity.py",
    ),
    BenchmarkCase(
        id="smoke_framework",
        question=(
            "Explain how run_recommended_smoke_suite exercises GraphSmokeIndex "
            "local and global search and RepoMapBuilder AST symbol retrieval."
        ),
        expected_paths=(
            "main_computer/rag_smoke_framework.py",
            "tests/test_rag_smoke_framework.py",
        ),
        expected_symbols=(
            "run_recommended_smoke_suite",
            "GraphSmokeIndex",
            "RepoMapBuilder",
            "build_repo_map",
        ),
        explain_node="run_recommended_smoke_suite",
        explain_source_path="main_computer/rag_smoke_framework.py",
        path_source="run_recommended_smoke_suite",
        path_source_path="main_computer/rag_smoke_framework.py",
        path_target="GraphSmokeIndex",
        path_target_path="main_computer/rag_smoke_framework.py",
    ),
    BenchmarkCase(
        id="route_to_v4",
        question=(
            "Trace how _handle_chat_console_rag_assisted_thinking_evaluate "
            "reaches run_rag_assisted_thinking_v4_request and the "
            "rag_assisted_thinking_v4 subprocess command mode."
        ),
        expected_paths=(
            "main_computer/viewport_routes_rag_assisted_thinking.py",
            "main_computer/rag_assisted_thinking_v4.py",
            "main_computer/chat_ai_subprocess.py",
            "main_computer/viewport_route_dispatch.py",
        ),
        expected_symbols=(
            "_handle_chat_console_rag_assisted_thinking_evaluate",
            "run_rag_assisted_thinking_v4_request",
            "rag_assisted_thinking_v4",
        ),
        explain_node="_handle_chat_console_rag_assisted_thinking_evaluate",
        explain_source_path="main_computer/viewport_routes_rag_assisted_thinking.py",
        path_source="_handle_chat_console_rag_assisted_thinking_evaluate",
        path_source_path="main_computer/viewport_routes_rag_assisted_thinking.py",
        path_target="run_rag_assisted_thinking_v4_request",
        path_target_path="main_computer/rag_assisted_thinking_v4.py",
    ),
)


REQUIRED_REPOSITORY_FILES: tuple[str, ...] = tuple(
    sorted(
        {
            path
            for case in CASES
            for path in case.expected_paths
        }
        | {
            "main_computer/rag_harness.py",
            "main_computer/rag_retriever.py",
            "main_computer/rag_smoke_framework.py",
            "tests/test_rag_retriever.py",
            "tests/test_rag_harness.py",
            "tests/test_rag_smoke_framework.py",
        }
    )
)


PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/])?[\w .@()$+\-\\/]+?"
    r"\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|json|md|toml|yaml|yml|ps1|sh|html|css))",
    re.IGNORECASE,
)
NODE_LINE_RE = re.compile(
    r"^\s*NODE\s+(?P<label>.*?)\s+\[src=(?P<src>.*?)\s+loc=.*?\]\s*$",
    re.IGNORECASE,
)
SOURCE_LINE_RE = re.compile(r"^\s*Source:\s*(?P<src>.+?)\s*$", re.IGNORECASE)


def normalize_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return re.sub(r"/+", "/", text)


def path_suffix_matches(candidate: str, expected: str) -> bool:
    left = normalize_path(candidate).lower().strip("/")
    right = normalize_path(expected).lower().strip("/")
    return bool(left and right and (left == right or left.endswith("/" + right)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def copy_file_verified(source: Path, destination: Path) -> None:
    """Stream-copy a file without Windows CopyFile2 and verify the bytes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".tmp-{os.getpid()}")
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256_file(source) != sha256_file(temporary):
            raise SmokeFailure(f"Copied graph failed SHA-256 verification: {source}")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def monitored_hashes(repo: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in REQUIRED_REPOSITORY_FILES:
        target = repo / relative
        if target.is_file():
            hashes[relative] = sha256_file(target)
    return hashes


def changed_monitored_files(repo: Path, before: Mapping[str, str]) -> list[str]:
    changed: list[str] = []
    for relative, digest in before.items():
        target = repo / relative
        if not target.is_file() or sha256_file(target) != digest:
            changed.append(relative)
    return changed


def parse_command_string(value: str) -> list[str]:
    # The common Windows-safe form is: --graphify-cmd "python -m graphify".
    parts = shlex.split(value, posix=(os.name != "nt"))
    cleaned = [part.strip('"') for part in parts if part.strip('"')]
    if not cleaned:
        raise SmokeFailure("--graphify-cmd must not be empty.")
    return cleaned


def command_candidates(explicit: str | None) -> list[list[str]]:
    if explicit:
        return [parse_command_string(explicit)]

    candidates: list[list[str]] = []
    executable = shutil.which("graphify")
    if executable:
        candidates.append([executable])
    candidates.append([sys.executable, "-m", "graphify"])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "argv": list(argv),
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "returncode": 124,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": str(exc.stdout or ""),
            "stderr": f"Timed out after {timeout} seconds.",
        }
    except OSError as exc:
        return {
            "argv": list(argv),
            "returncode": 127,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def resolve_graphify_command(
    explicit: str | None,
    *,
    repo: Path,
    timeout: int,
) -> tuple[list[str], str, list[dict[str, Any]]]:
    probes: list[dict[str, Any]] = []
    for candidate in command_candidates(explicit):
        result = run_process([*candidate, "--version"], cwd=repo, timeout=min(timeout, 30))
        probes.append(result)
        if result["returncode"] == 0:
            version = (result["stdout"] or result["stderr"]).strip()
            return candidate, version, probes

    details = "\n".join(
        f"{' '.join(item['argv'])} -> exit {item['returncode']}: "
        f"{(item['stderr'] or item['stdout']).strip()}"
        for item in probes
    )
    raise SmokeFailure(f"No usable Graphify command found.\n{details}")


def load_graph(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(f"Graph file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Graph JSON is malformed: {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise SmokeFailure("Graph JSON top level must be an object.")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise SmokeFailure("Graph JSON must contain a list-valued 'nodes' collection.")

    if isinstance(payload.get("links"), list):
        edges = payload["links"]
        edge_key = "links"
    elif isinstance(payload.get("edges"), list):
        edges = payload["edges"]
        edge_key = "edges"
    else:
        raise SmokeFailure("Graph JSON must contain list-valued 'links' or 'edges'.")

    clean_nodes = [item for item in nodes if isinstance(item, dict)]
    clean_edges = [item for item in edges if isinstance(item, dict)]
    if not clean_nodes or not clean_edges:
        raise SmokeFailure("Graph must contain at least one node and one edge.")
    return payload, clean_nodes, clean_edges, edge_key


def node_id(node: Mapping[str, Any]) -> str:
    return str(node.get("id") or node.get("key") or "").strip()


def node_label(node: Mapping[str, Any]) -> str:
    return str(
        node.get("label")
        or node.get("name")
        or node.get("title")
        or node_id(node)
    ).strip()


def node_source(node: Mapping[str, Any]) -> str:
    return normalize_path(
        node.get("source_file")
        or node.get("source")
        or node.get("path")
        or ""
    )


def node_search_text(node: Mapping[str, Any]) -> str:
    values = [
        node_id(node),
        node_label(node),
        node_source(node),
        str(node.get("source_location") or ""),
        str(node.get("type") or ""),
        str(node.get("kind") or ""),
    ]
    return " ".join(values).lower()


def edge_endpoint(edge: Mapping[str, Any], side: str) -> str:
    value = edge.get(side)
    if isinstance(value, Mapping):
        value = value.get("id") or value.get("key") or value.get("label")
    return str(value or "").strip()


def edge_relation(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("relation")
        or edge.get("type")
        or edge.get("label")
        or "related_to"
    ).strip()


def split_identifier_tokens(value: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    text = text.replace("_", " ").replace("-", " ")
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", text)
        if len(token) >= 3
    ]


def find_best_node(
    nodes: Sequence[Mapping[str, Any]],
    query: str,
    preferred_source: str | None = None,
) -> str | None:
    query_lower = query.lower()
    query_tokens = split_identifier_tokens(query)
    scored: list[tuple[float, str]] = []

    for node in nodes:
        identifier = node_id(node)
        if not identifier:
            continue
        label = node_label(node)
        source = node_source(node)
        haystack = f"{label} {identifier} {source}".lower()

        score = 0.0
        if label.lower() == query_lower:
            score += 100.0
        if identifier.lower() == query_lower:
            score += 90.0
        if query_lower in label.lower():
            score += 40.0
        if query_lower in identifier.lower():
            score += 30.0
        if preferred_source and path_suffix_matches(source, preferred_source):
            score += 200.0
        for token in query_tokens:
            if token in haystack:
                score += 3.0
        if score:
            scored.append((score, identifier))

    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def shortest_graph_path(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    source_query: str,
    target_query: str,
    *,
    source_path: str | None = None,
    target_path: str | None = None,
    max_hops: int = 12,
) -> dict[str, Any]:
    by_id = {node_id(node): node for node in nodes if node_id(node)}
    source = find_best_node(nodes, source_query, source_path)
    target = find_best_node(nodes, target_query, target_path)
    if not source or not target:
        return {
            "found": False,
            "source_node": source,
            "target_node": target,
            "reason": "anchor_not_found",
            "hops": None,
            "labels": [],
            "relations": [],
        }

    adjacency: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for edge in edges:
        left = edge_endpoint(edge, "source")
        right = edge_endpoint(edge, "target")
        if left not in by_id or right not in by_id:
            continue
        relation = edge_relation(edge)
        adjacency[left].append((right, relation))
        adjacency[right].append((left, relation))

    queue = collections.deque([source])
    parent: dict[str, tuple[str, str] | None] = {source: None}
    depth: dict[str, int] = {source: 0}
    while queue:
        current = queue.popleft()
        if current == target:
            break
        if depth[current] >= max_hops:
            continue
        for neighbor, relation in adjacency.get(current, []):
            if neighbor in parent:
                continue
            parent[neighbor] = (current, relation)
            depth[neighbor] = depth[current] + 1
            queue.append(neighbor)

    if target not in parent:
        return {
            "found": False,
            "source_node": source,
            "target_node": target,
            "reason": "no_path",
            "hops": None,
            "labels": [node_label(by_id[source]), node_label(by_id[target])],
            "relations": [],
        }

    identifiers: list[str] = []
    relations: list[str] = []
    cursor = target
    while cursor != source:
        identifiers.append(cursor)
        previous, relation = parent[cursor]  # type: ignore[misc]
        relations.append(relation)
        cursor = previous
    identifiers.append(source)
    identifiers.reverse()
    relations.reverse()

    return {
        "found": True,
        "source_node": source,
        "target_node": target,
        "reason": "",
        "hops": len(identifiers) - 1,
        "labels": [node_label(by_id[item]) for item in identifiers],
        "relations": relations,
    }


def graph_gold_inventory(
    nodes: Sequence[Mapping[str, Any]],
    case: BenchmarkCase,
) -> dict[str, Any]:
    path_hits: list[str] = []
    symbol_hits: list[str] = []
    node_texts = [node_search_text(node) for node in nodes]
    sources = [node_source(node) for node in nodes if node_source(node)]

    for expected in case.expected_paths:
        if any(path_suffix_matches(source, expected) for source in sources):
            path_hits.append(expected)
    for expected in case.expected_symbols:
        lowered = expected.lower()
        if any(lowered in text for text in node_texts):
            symbol_hits.append(expected)

    return {
        "expected_path_hits": path_hits,
        "expected_symbol_hits": symbol_hits,
        "path_recall": ratio(len(path_hits), len(case.expected_paths)),
        "symbol_recall": ratio(len(symbol_hits), len(case.expected_symbols)),
    }


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 1.0


def score_lane(
    *,
    expected_paths: Sequence[str],
    expected_symbols: Sequence[str],
    evidence_paths: Iterable[str],
    evidence_text: str,
) -> dict[str, Any]:
    normalized_evidence_paths = sorted(
        {normalize_path(path) for path in evidence_paths if normalize_path(path)}
    )
    path_hits = [
        expected
        for expected in expected_paths
        if any(path_suffix_matches(candidate, expected) for candidate in normalized_evidence_paths)
        or normalize_path(expected).lower() in evidence_text.lower().replace("\\", "/")
    ]
    symbol_hits = [
        expected
        for expected in expected_symbols
        if expected.lower() in evidence_text.lower()
    ]
    path_recall = ratio(len(path_hits), len(expected_paths))
    symbol_recall = ratio(len(symbol_hits), len(expected_symbols))
    return {
        "evidence_paths": normalized_evidence_paths,
        "expected_path_hits": path_hits,
        "expected_symbol_hits": symbol_hits,
        "path_recall": path_recall,
        "symbol_recall": symbol_recall,
        "evidence_score": round((path_recall + symbol_recall) / 2.0, 3),
    }


def extract_paths_from_text(text: str) -> list[str]:
    paths: set[str] = set()
    for line in text.splitlines():
        node_match = NODE_LINE_RE.match(line)
        if node_match:
            source = normalize_path(node_match.group("src"))
            if source:
                paths.add(source)
        source_match = SOURCE_LINE_RE.match(line)
        if source_match:
            source = normalize_path(source_match.group("src"))
            if source:
                paths.add(source)
        for match in PATH_RE.finditer(line):
            value = normalize_path(match.group("path")).strip(" ,;:()[]{}'\"")
            if value:
                paths.add(value)
    return sorted(paths)


def import_repository_modules(repo: Path) -> tuple[Any, Any]:
    repo_text = str(repo)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)

    try:
        rag_harness = importlib.import_module("main_computer.rag_harness")
        rag_smoke = importlib.import_module("main_computer.rag_smoke_framework")
    except Exception as exc:
        raise SmokeFailure(
            "Could not import the repository's RAG modules. Run this script with "
            "the repository virtual environment active.\n"
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not hasattr(rag_harness, "run_rag_harness"):
        raise SmokeFailure("main_computer.rag_harness lacks run_rag_harness.")
    if not hasattr(rag_smoke, "run_recommended_smoke_suite"):
        raise SmokeFailure(
            "main_computer.rag_smoke_framework lacks run_recommended_smoke_suite."
        )
    return rag_harness, rag_smoke


def run_existing_rag_lane(
    case: BenchmarkCase,
    *,
    rag_harness: Any,
    repo: Path,
    output_root: Path,
    max_context_chars: int,
    max_candidates: int,
    max_chunks: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = rag_harness.run_rag_harness(
        prompt=case.question,
        repo_dir=repo,
        queries=[case.question],
        output_root=output_root,
        max_context_chars=max_context_chars,
        max_candidates=max_candidates,
        max_chunks=max_chunks,
        use_model=False,
        run_id=case.id,
    )
    elapsed = round(time.perf_counter() - started, 3)

    retrieval = result.retrieval
    candidates = [candidate.as_dict() for candidate in retrieval.candidates]
    chunks = [chunk.as_dict() for chunk in retrieval.chunks]
    evidence_paths = [
        *[item["path"] for item in candidates],
        *[item["path"] for item in chunks],
    ]
    evidence_text = "\n".join(
        [
            json.dumps(result.task_decomposition, ensure_ascii=False),
            json.dumps(result.context_brief, ensure_ascii=False),
            json.dumps(result.final_plan, ensure_ascii=False),
            *[str(item.get("content") or "") for item in chunks],
        ]
    )
    score = score_lane(
        expected_paths=case.expected_paths,
        expected_symbols=case.expected_symbols,
        evidence_paths=evidence_paths,
        evidence_text=evidence_text,
    )

    return {
        "ok": bool(result.ok),
        "status": result.status,
        "elapsed_seconds": elapsed,
        "scanned_files": retrieval.scanned_files,
        "candidate_count": len(candidates),
        "chunk_count": len(chunks),
        "context_budget_chars": retrieval.context_budget_chars,
        "used_context_chars": retrieval.used_chars,
        "top_candidates": candidates[:12],
        "chunk_paths": [item["path"] for item in chunks],
        "final_plan": result.final_plan,
        **score,
    }


def graphify_output_has_failure(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "could not find",
        "no path found",
        "node not found",
        "no node matching",
        "no nodes found",
        "graph not found",
        "error:",
    )
    return any(marker in lowered for marker in markers)


def run_graphify_lane(
    case: BenchmarkCase,
    *,
    command: Sequence[str],
    repo: Path,
    graph: Path,
    budget: int,
    timeout: int,
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    # Resolve source-qualified graph anchors first. The repository contains smoke
    # fixtures that mention or even define some of the same symbol names, so a bare
    # label can be ambiguous. Graphify's path command accepts exact node IDs.
    explain_anchor = find_best_node(
        nodes,
        case.explain_node,
        case.explain_source_path,
    )
    source_anchor = find_best_node(
        nodes,
        case.path_source,
        case.path_source_path,
    )
    target_anchor = find_best_node(
        nodes,
        case.path_target,
        case.path_target_path,
    )
    if not explain_anchor or not source_anchor or not target_anchor:
        missing = {
            "explain": explain_anchor,
            "path_source": source_anchor,
            "path_target": target_anchor,
        }
        raise SmokeFailure(
            f"Graph is missing source-qualified anchors for {case.id}: {missing}"
        )

    commands = [
        (
            "query",
            [
                *command,
                "query",
                case.question,
                "--budget",
                str(budget),
                "--graph",
                str(graph),
            ],
        ),
        (
            "explain",
            [
                *command,
                "explain",
                explain_anchor,
                "--graph",
                str(graph),
            ],
        ),
        (
            "path",
            [
                *command,
                "path",
                source_anchor,
                target_anchor,
                "--graph",
                str(graph),
            ],
        ),
    ]

    results: dict[str, dict[str, Any]] = {}
    combined_parts: list[str] = []
    for name, argv in commands:
        result = run_process(argv, cwd=graph.parent, timeout=timeout)
        results[name] = result
        # This is the context an assistant would consume. Diagnostics on stderr
        # are retained in the command record but do not receive evidence credit.
        combined_parts.append(result["stdout"])

    combined = "\n".join(combined_parts)
    evidence_paths = extract_paths_from_text(combined)
    score = score_lane(
        expected_paths=case.expected_paths,
        expected_symbols=case.expected_symbols,
        evidence_paths=evidence_paths,
        evidence_text=combined,
    )

    query_result = results["query"]
    explain_result = results["explain"]
    path_result = results["path"]

    query_ok = (
        query_result["returncode"] == 0
        and bool(query_result["stdout"].strip())
        and not graphify_output_has_failure(query_result["stdout"])
    )
    explain_ok = (
        explain_result["returncode"] == 0
        and bool(explain_result["stdout"].strip())
        and not graphify_output_has_failure(explain_result["stdout"])
    )
    path_ok = (
        path_result["returncode"] == 0
        and (
            "shortest path" in path_result["stdout"].lower()
            or " hops" in path_result["stdout"].lower()
        )
        and not graphify_output_has_failure(path_result["stdout"])
    )

    direct_path = shortest_graph_path(
        nodes,
        edges,
        case.path_source,
        case.path_target,
        source_path=case.path_source_path,
        target_path=case.path_target_path,
    )

    return {
        "ok": query_ok and explain_ok and path_ok,
        "query_ok": query_ok,
        "explain_ok": explain_ok,
        "path_ok": path_ok,
        "elapsed_seconds": round(
            sum(float(item["elapsed_seconds"]) for item in results.values()), 3
        ),
        "output_chars": len(combined),
        "resolved_anchors": {
            "explain_node_id": explain_anchor,
            "path_source_node_id": source_anchor,
            "path_target_node_id": target_anchor,
        },
        "commands": results,
        "direct_graph_path": direct_path,
        **score,
    }


def run_existing_smoke_suite(rag_smoke: Any, output_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    outcomes = rag_smoke.run_recommended_smoke_suite(output_dir)
    elapsed = round(time.perf_counter() - started, 3)
    payload = [
        outcome.as_dict() if hasattr(outcome, "as_dict") else asdict(outcome)
        for outcome in outcomes
    ]
    return {
        "ok": all(bool(item.get("ok")) for item in payload),
        "count": len(payload),
        "passed": sum(1 for item in payload if item.get("ok")),
        "elapsed_seconds": elapsed,
        "outcomes": payload,
    }


def determine_case_winner(existing: Mapping[str, Any], graphify: Mapping[str, Any]) -> str:
    left = float(existing["evidence_score"])
    right = float(graphify["evidence_score"])
    if right > left:
        return "graphify"
    if left > right:
        return "existing_rag"
    left_chars = int(existing.get("used_context_chars") or 0)
    right_chars = int(graphify.get("output_chars") or 0)
    if left_chars and right_chars:
        if right_chars < left_chars:
            return "graphify_on_smaller_context"
        if left_chars < right_chars:
            return "existing_rag_on_smaller_context"
    return "tie"


def aggregate_cases(case_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(case_results)
    if not count:
        return {}

    def average(lane: str, field: str) -> float:
        return round(
            sum(float(item[lane][field]) for item in case_results) / count,
            3,
        )

    winners = collections.Counter(str(item["winner"]) for item in case_results)
    graphify_path_success = sum(
        1 for item in case_results if item["graphify"]["path_ok"]
    )
    return {
        "case_count": count,
        "existing_rag": {
            "average_path_recall": average("existing_rag", "path_recall"),
            "average_symbol_recall": average("existing_rag", "symbol_recall"),
            "average_evidence_score": average("existing_rag", "evidence_score"),
            "total_elapsed_seconds": round(
                sum(float(item["existing_rag"]["elapsed_seconds"]) for item in case_results),
                3,
            ),
            "total_context_chars": sum(
                int(item["existing_rag"]["used_context_chars"]) for item in case_results
            ),
        },
        "graphify": {
            "average_path_recall": average("graphify", "path_recall"),
            "average_symbol_recall": average("graphify", "symbol_recall"),
            "average_evidence_score": average("graphify", "evidence_score"),
            "path_success_count": graphify_path_success,
            "total_elapsed_seconds": round(
                sum(float(item["graphify"]["elapsed_seconds"]) for item in case_results),
                3,
            ),
            "total_output_chars": sum(
                int(item["graphify"]["output_chars"]) for item in case_results
            ),
        },
        "winners": dict(sorted(winners.items())),
    }


def markdown_report(report: Mapping[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# Graphify vs existing RAG smoke report",
        "",
        f"- Status: **{report['status']}**",
        f"- Repository: `{report['repo']}`",
        f"- Graph: `{report['graph']['path']}`",
        f"- Graphify: `{report['graphify_version']}`",
        (
            f"- Graph size: {report['graph']['node_count']:,} nodes, "
            f"{report['graph']['edge_count']:,} edges "
            f"(`{report['graph']['edge_collection_key']}`)"
        ),
        (
            f"- Existing deterministic smoke suite: "
            f"{report['existing_smoke_suite']['passed']}/"
            f"{report['existing_smoke_suite']['count']} passed"
        ),
        "",
        "## A/B cases",
        "",
        "| Case | Existing RAG evidence | Graphify evidence | Graph path | Existing chars | Graphify chars | Winner |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]

    for item in report["cases"]:
        existing = item["existing_rag"]
        graphify = item["graphify"]
        lines.append(
            "| {case} | {left:.3f} | {right:.3f} | {path} | {left_chars:,} | "
            "{right_chars:,} | {winner} |".format(
                case=item["id"],
                left=float(existing["evidence_score"]),
                right=float(graphify["evidence_score"]),
                path="yes" if graphify["path_ok"] else "no",
                left_chars=int(existing["used_context_chars"]),
                right_chars=int(graphify["output_chars"]),
                winner=item["winner"],
            )
        )

    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            (
                f"- Existing RAG average evidence score: "
                f"**{aggregate['existing_rag']['average_evidence_score']:.3f}**"
            ),
            (
                f"- Graphify average evidence score: "
                f"**{aggregate['graphify']['average_evidence_score']:.3f}**"
            ),
            (
                f"- Graphify successful relation paths: "
                f"**{aggregate['graphify']['path_success_count']}/"
                f"{aggregate['case_count']}**"
            ),
            (
                f"- Existing RAG context supplied: "
                f"**{aggregate['existing_rag']['total_context_chars']:,} chars**"
            ),
            (
                f"- Graphify scoped output supplied: "
                f"**{aggregate['graphify']['total_output_chars']:,} chars**"
            ),
            "",
            "## Interpretation",
            "",
            (
                "This is a retrieval and relationship-tracing benchmark, not an "
                "LLM answer-quality benchmark. The existing lane runs the repository's "
                "`run_rag_harness(..., use_model=False)`. The Graphify lane runs "
                "`query`, `explain`, and `path` against the prebuilt graph and does "
                "not pass the repository path to those commands."
            ),
            "",
        ]
    )

    if report.get("warnings"):
        lines.extend(["## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)


def resolve_output_path(repo: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the repository's existing deterministic RAG harness with "
            "Claude-style Graphify query/explain/path traversal."
        )
    )
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument(
        "--graph",
        default="graphify-repo-graph.json",
        help="Clustered or raw Graphify graph JSON.",
    )
    parser.add_argument(
        "--graphify-cmd",
        help=(
            "Optional quoted command prefix, for example "
            "'graphify' or 'python -m graphify'."
        ),
    )
    parser.add_argument(
        "--json-out",
        default="graphify-vs-rag-report.json",
        help="Persistent machine-readable report.",
    )
    parser.add_argument(
        "--markdown-out",
        default="graphify-vs-rag-report.md",
        help="Persistent human-readable report.",
    )
    parser.add_argument("--graphify-budget", type=int, default=2000)
    parser.add_argument("--max-context-chars", type=int, default=30000)
    parser.add_argument("--max-candidates", type=int, default=24)
    parser.add_argument("--max-chunks", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--require-graphify-wins",
        action="store_true",
        help=(
            "Return a failing exit code unless Graphify's average evidence score "
            "is strictly greater than the existing RAG score."
        ),
    )
    parser.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Keep temporary baseline artifacts and smoke traces.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "graphify_budget",
        "max_context_chars",
        "max_candidates",
        "max_chunks",
        "timeout",
    ):
        if int(getattr(args, name)) <= 0:
            raise SmokeFailure(f"--{name.replace('_', '-')} must be greater than zero.")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    validate_args(args)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SmokeFailure(f"Repository does not exist or is not a directory: {repo}")

    missing_files = [
        relative
        for relative in REQUIRED_REPOSITORY_FILES
        if not (repo / relative).is_file()
    ]
    if missing_files:
        raise SmokeFailure(
            "Repository is missing required benchmark files:\n"
            + "\n".join(f"  - {item}" for item in missing_files)
        )

    graph = resolve_output_path(repo, args.graph)
    json_out = resolve_output_path(repo, args.json_out)
    markdown_out = resolve_output_path(repo, args.markdown_out)

    graph_payload, nodes, edges, edge_key = load_graph(graph)
    command, version, command_probes = resolve_graphify_command(
        args.graphify_cmd,
        repo=repo,
        timeout=args.timeout,
    )
    rag_harness, rag_smoke = import_repository_modules(repo)

    before_hashes = monitored_hashes(repo)
    work_dir = Path(
        tempfile.mkdtemp(prefix="graphify-vs-existing-rag-smoke-")
    ).resolve()
    working_graph = work_dir / "graphify-lane" / "graph.json"
    copy_file_verified(graph, working_graph)
    warnings: list[str] = []
    report: dict[str, Any] = {
        "status": "running",
        "repo": str(repo),
        "work_dir": str(work_dir),
        "graphify_command": command,
        "graphify_version": version,
        "command_probes": command_probes,
        "graph": {
            "path": str(graph),
            "sha256": sha256_file(graph),
            "size_bytes": graph.stat().st_size,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "edge_collection_key": edge_key,
            "directed": bool(graph_payload.get("directed")),
            "working_copy": str(working_graph),
            "commands_use_working_copy": True,
        },
        "questions_target": "existing_rag_and_smoke_code",
        "comparison": {
            "existing_lane": "run_rag_harness(use_model=False)",
            "graphify_lane": "query + explain + path against an isolated graph copy",
            "same_questions": True,
            "llm_used": False,
        },
        "cases": [],
        "warnings": warnings,
    }

    exit_code = 0
    try:
        smoke_dir = work_dir / "existing-smoke-suite"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        report["existing_smoke_suite"] = run_existing_smoke_suite(
            rag_smoke,
            smoke_dir,
        )

        graph_inventory: dict[str, Any] = {}
        for case in CASES:
            graph_inventory[case.id] = graph_gold_inventory(nodes, case)
        report["graph_gold_inventory"] = graph_inventory

        baseline_root = work_dir / "existing-rag-runs"
        baseline_root.mkdir(parents=True, exist_ok=True)

        case_results: list[dict[str, Any]] = []
        for case in CASES:
            existing = run_existing_rag_lane(
                case,
                rag_harness=rag_harness,
                repo=repo,
                output_root=baseline_root,
                max_context_chars=args.max_context_chars,
                max_candidates=args.max_candidates,
                max_chunks=args.max_chunks,
            )
            graphify = run_graphify_lane(
                case,
                command=command,
                repo=repo,
                graph=working_graph,
                budget=args.graphify_budget,
                timeout=args.timeout,
                nodes=nodes,
                edges=edges,
            )
            case_payload = {
                "id": case.id,
                "question": case.question,
                "expected_paths": list(case.expected_paths),
                "expected_symbols": list(case.expected_symbols),
                "explain_node": case.explain_node,
                "path_source": case.path_source,
                "path_target": case.path_target,
                "graph_inventory": graph_inventory[case.id],
                "existing_rag": existing,
                "graphify": graphify,
                "winner": determine_case_winner(existing, graphify),
            }
            case_results.append(case_payload)

        report["cases"] = case_results
        report["aggregate"] = aggregate_cases(case_results)

        changed = changed_monitored_files(repo, before_hashes)
        report["source_repository_modified"] = bool(changed)
        report["modified_monitored_files"] = changed
        report["monitored_source_file_count"] = len(before_hashes)

        infrastructure_ok = (
            report["existing_smoke_suite"]["ok"]
            and not changed
            and all(item["graphify"]["query_ok"] for item in case_results)
            and all(item["graphify"]["explain_ok"] for item in case_results)
        )
        path_successes = report["aggregate"]["graphify"]["path_success_count"]
        evidence_score = report["aggregate"]["graphify"]["average_evidence_score"]

        if not infrastructure_ok:
            report["status"] = "failed"
            exit_code = 2
        elif path_successes < max(1, len(CASES) // 2):
            report["status"] = "failed_graph_paths"
            warnings.append(
                "Graphify resolved fewer than half of the expected relation paths."
            )
            exit_code = 2
        elif evidence_score < 0.5:
            report["status"] = "failed_graph_evidence"
            warnings.append(
                "Graphify surfaced less than 50% average gold evidence."
            )
            exit_code = 2
        else:
            existing_score = report["aggregate"]["existing_rag"][
                "average_evidence_score"
            ]
            if evidence_score > existing_score:
                report["status"] = "passed_graphify_advantage"
            elif evidence_score == existing_score:
                report["status"] = "passed_tie"
            else:
                report["status"] = "passed_existing_rag_advantage"

        if args.require_graphify_wins and (
            report["aggregate"]["graphify"]["average_evidence_score"]
            <= report["aggregate"]["existing_rag"]["average_evidence_score"]
        ):
            report["status"] = "failed_graphify_did_not_win"
            warnings.append(
                "--require-graphify-wins was set, but Graphify did not achieve "
                "a strictly greater average evidence score."
            )
            exit_code = 3

    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["source_repository_modified"] = bool(
            changed_monitored_files(repo, before_hashes)
        )
        report["modified_monitored_files"] = changed_monitored_files(
            repo, before_hashes
        )
        exit_code = 2
    finally:
        atomic_write_text(
            json_out,
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        )
        atomic_write_text(markdown_out, markdown_report(report) if "aggregate" in report else (
            "# Graphify vs existing RAG smoke report\n\n"
            f"- Status: **{report.get('status', 'failed')}**\n"
            f"- Error: `{report.get('error', 'unknown failure')}`\n"
        ))
        report["output_artifacts"] = {
            "json_report": str(json_out),
            "markdown_report": str(markdown_out),
        }
        # Re-write JSON once with output_artifacts included.
        atomic_write_text(
            json_out,
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        )

        if args.keep_work_dir:
            print(f"Work artifacts kept at: {work_dir}", file=sys.stderr)
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

    smoke_summary = report.get("existing_smoke_suite") or {}
    print(json.dumps(
        {
            "status": report.get("status"),
            "graphify_version": version,
            "graph": report.get("graph"),
            "existing_smoke_suite": {
                "ok": smoke_summary.get("ok"),
                "passed": smoke_summary.get("passed"),
                "count": smoke_summary.get("count"),
                "elapsed_seconds": smoke_summary.get("elapsed_seconds"),
            },
            "aggregate": report.get("aggregate"),
            "source_repository_modified": report.get("source_repository_modified"),
            "output_artifacts": report.get("output_artifacts"),
            "warnings": report.get("warnings"),
            "error": report.get("error"),
        },
        indent=2,
        ensure_ascii=False,
    ))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
