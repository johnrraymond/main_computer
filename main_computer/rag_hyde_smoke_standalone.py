#!/usr/bin/env python3
"""
Standalone HyDE RAG smoke test.

What this proves:
- A vague user query can retrieve the wrong "surface language" document.
- A HyDE-style hypothetical document can add concrete retrieval terms.
- Retrieval with the hypothetical document finds the real root-cause document.

No external dependencies.
No model calls.
No repo imports.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re
import sys
from typing import Iterable


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "by",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "why",
    "with",
}


@dataclass(frozen=True)
class Document:
    path: str
    title: str
    text: str


@dataclass(frozen=True)
class Hit:
    path: str
    score: float
    matched_terms: list[str]
    snippet: str


@dataclass(frozen=True)
class HyDESmokeTrace:
    query: str
    hypothetical_document: str
    baseline_top: str
    hyde_top: str
    baseline_hits: list[Hit]
    hyde_hits: list[Hit]


def normalize_token(token: str) -> str:
    token = token.lower().strip("_-")

    # Tiny deterministic stemmer, just enough for this smoke test.
    # showing -> show, loading -> load, manifests -> manifest
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    if len(token) > 4 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 4 and token.endswith("s"):
        token = token[:-1]

    return token


def tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*", text.lower())
    tokens = [normalize_token(token) for token in raw_tokens]
    return [token for token in tokens if token and token not in STOPWORDS]


def unique_in_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def score_document(query: str, document: Document) -> Hit:
    query_terms = tokenize(query)
    doc_text = f"{document.title}\n{document.text}"
    doc_terms = set(tokenize(doc_text))

    matched_terms = unique_in_order(
        term for term in query_terms if term in doc_terms
    )

    # Deliberately simple:
    # - unique term overlap is the main signal
    # - repeated matches in the query get a tiny extra weight
    # - title matches get a small boost
    unique_overlap = len(set(matched_terms))
    repeated_overlap = sum(1 for term in query_terms if term in doc_terms)

    title_terms = set(tokenize(document.title))
    title_overlap = sum(1 for term in set(query_terms) if term in title_terms)

    score = unique_overlap * 10.0 + repeated_overlap * 1.0 + title_overlap * 3.0

    return Hit(
        path=document.path,
        score=score,
        matched_terms=matched_terms,
        snippet=document.text[:240].replace("\n", " "),
    )


def retrieve(query: str, corpus: list[Document], *, top_k: int = 3) -> list[Hit]:
    hits = [score_document(query, document) for document in corpus]
    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[:top_k]


def generate_hypothetical_document(query: str) -> str:
    """
    Deterministic HyDE stand-in.

    Real HyDE asks a model to write a hypothetical answer/document, then retrieves
    using that generated text. This fake version is intentionally small and stable
    so it can run as a smoke test without network or model access.
    """
    normalized = " ".join(tokenize(query))

    if {"app", "forever", "ui"} & set(normalized.split()):
        return (
            "A likely root cause for slow application startup is plugin discovery. "
            "During launch, the application scans extension manifests, loads "
            "dependency hooks, and initializes plugin metadata before rendering "
            "the first window. Excessive extension manifests or dependency hooks "
            "can cause startup latency before the UI appears."
        )

    return (
        "The issue likely depends on concrete implementation details, logs, "
        "configuration, dependency initialization, and runtime behavior."
    )


def retrieve_with_hyde(
    query: str,
    corpus: list[Document],
    *,
    top_k: int = 3,
) -> HyDESmokeTrace:
    baseline_hits = retrieve(query, corpus, top_k=top_k)
    hypothetical_document = generate_hypothetical_document(query)

    # HyDE retrieval usually searches with the generated hypothetical document.
    # We include the original query too, but the hypothetical document supplies
    # the concrete terms that the vague query lacks.
    hyde_query = f"{query}\n\n{hypothetical_document}"
    hyde_hits = retrieve(hyde_query, corpus, top_k=top_k)

    return HyDESmokeTrace(
        query=query,
        hypothetical_document=hypothetical_document,
        baseline_top=baseline_hits[0].path,
        hyde_top=hyde_hits[0].path,
        baseline_hits=baseline_hits,
        hyde_hits=hyde_hits,
    )


def build_fixture_corpus() -> list[Document]:
    return [
        Document(
            path="docs/ui_spinner.md",
            title="UI loading spinner behavior",
            text=(
                "The UI loading screen shows a spinner while the app is waiting "
                "for the first view. If the app takes a long time before showing "
                "the UI, the spinner continues to animate. This document only "
                "describes visible loading feedback."
            ),
        ),
        Document(
            path="docs/plugin_startup.md",
            title="Plugin startup performance",
            text=(
                "Startup latency comes from plugin discovery. At boot, the "
                "application scans extension manifests and loads dependency hooks "
                "before rendering the first window. Slow extension manifests can "
                "delay launch before the UI appears."
            ),
        ),
        Document(
            path="docs/network_timeout.md",
            title="Network timeout behavior",
            text=(
                "Network requests use a thirty second timeout. Failed requests "
                "are retried twice. This policy does not control launch-time "
                "plugin initialization."
            ),
        ),
        Document(
            path="docs/theme_renderer.md",
            title="Theme renderer",
            text=(
                "The theme renderer chooses colors, spacing, and typography after "
                "the first window exists. It does not scan extensions or load "
                "dependency hooks."
            ),
        ),
    ]


def run_smoke() -> HyDESmokeTrace:
    query = "Why does the app take forever before showing the UI?"
    corpus = build_fixture_corpus()
    return retrieve_with_hyde(query, corpus)


def test_baseline_retrieval_picks_surface_language_decoy() -> None:
    trace = run_smoke()

    assert trace.baseline_top == "docs/ui_spinner.md"
    assert trace.baseline_hits[0].path == "docs/ui_spinner.md"
    assert "ui" in trace.baseline_hits[0].matched_terms
    assert "app" in trace.baseline_hits[0].matched_terms


def test_hyde_retrieval_picks_root_cause_document() -> None:
    trace = run_smoke()

    assert trace.baseline_top == "docs/ui_spinner.md"
    assert trace.hyde_top == "docs/plugin_startup.md"

    top = trace.hyde_hits[0]
    assert "plugin" in top.matched_terms
    assert "discovery" in top.matched_terms
    assert "extension" in top.matched_terms
    assert "manifest" in top.matched_terms
    assert "dependency" in top.matched_terms
    assert "hook" in top.matched_terms


def test_hyde_trace_is_machine_checkable() -> None:
    trace = run_smoke()

    payload = asdict(trace)
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    decoded = json.loads(encoded)

    assert decoded["query"] == "Why does the app take forever before showing the UI?"
    assert decoded["baseline_top"] == "docs/ui_spinner.md"
    assert decoded["hyde_top"] == "docs/plugin_startup.md"
    assert "plugin discovery" in decoded["hypothetical_document"].lower()


def main() -> int:
    tests = [
        test_baseline_retrieval_picks_surface_language_decoy,
        test_hyde_retrieval_picks_root_cause_document,
        test_hyde_trace_is_machine_checkable,
    ]

    for test in tests:
        test()

    trace = run_smoke()
    print("HyDE smoke test passed.")
    print()
    print(json.dumps(asdict(trace), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print("HyDE smoke test failed.", file=sys.stderr)
        if str(exc):
            print(str(exc), file=sys.stderr)
        raise SystemExit(1)