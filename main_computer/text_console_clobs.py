from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


CLOB_SCHEMA_VERSION = 1
DEFAULT_TERMINAL_CLOB_THRESHOLD_CHARS = 8000
DEFAULT_CLOB_LOOKUP_MAX_CHARS = 3600

CLOB_ID_RE = re.compile(r"\bclob-[A-Za-z0-9][A-Za-z0-9_.-]{8,}\b")
WORD_RE = re.compile(r"[A-Za-z0-9_./-]+")

STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "before",
    "could",
    "does",
    "from",
    "have",
    "into",
    "like",
    "list",
    "make",
    "more",
    "need",
    "now",
    "only",
    "please",
    "prior",
    "result",
    "show",
    "that",
    "their",
    "there",
    "these",
    "thing",
    "this",
    "those",
    "through",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}


@dataclass(frozen=True)
class TextConsoleClobLookupResult:
    clob_id: str
    line_number: int
    text: str
    score: int

    def to_payload(self, evidence_id: str) -> dict[str, Any]:
        return {
            "evidence_id": evidence_id,
            "clob_id": self.clob_id,
            "line_number": self.line_number,
            "text": self.text,
            "score": self.score,
        }


def text_console_clob_root(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / "diagnostics_output" / "text_console_clobs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _safe_clob_type(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "text").strip()).strip("-") or "text"


def _relative_cache_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def save_text_console_clob(
    repo_root: str | Path,
    *,
    clob_type: str,
    text: str,
    source: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a large text payload as a side-loaded text-console clob.

    The clob id is content-addressed by type and payload.  Runtime metadata is
    intentionally descriptive only; it is not needed to recover the text.
    """

    root = Path(repo_root).resolve()
    payload_text = str(text or "")
    payload_sha256 = _sha256_text(payload_text)
    safe_type = _safe_clob_type(clob_type)
    clob_id = f"clob-{safe_type}-{payload_sha256[:16]}"
    cache_dir = text_console_clob_root(root) / safe_type
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{clob_id}.clob.json"
    line_count = len(payload_text.splitlines())
    payload = {
        "schema_version": CLOB_SCHEMA_VERSION,
        "clob_id": clob_id,
        "clob_type": safe_type,
        "created_at": _utc_now(),
        "payload_sha256": payload_sha256,
        "text_chars": len(payload_text),
        "line_count": line_count,
        "source": source or {},
        "metadata": metadata or {},
        "payload": {
            "encoding": "utf-8",
            "text": payload_text,
        },
    }
    if cache_path.exists():
        # Keep content-addressed clobs stable across requests.  Do not rewrite
        # created_at just because the same payload was observed again.
        try:
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            if existing.get("payload_sha256") == payload_sha256:
                payload = existing
        except Exception:
            pass
    else:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "clob_id": clob_id,
        "clob_type": safe_type,
        "payload_sha256": payload_sha256,
        "text_chars": len(payload_text),
        "line_count": line_count,
        "cache_path": _relative_cache_path(root, cache_path),
    }


def load_text_console_clob(repo_root: str | Path, clob_id: str) -> dict[str, Any] | None:
    raw_id = str(clob_id or "").strip()
    if not CLOB_ID_RE.fullmatch(raw_id):
        return None
    base = text_console_clob_root(repo_root)
    if not base.exists():
        return None
    matches = list(base.glob(f"*/{raw_id}.clob.json"))
    if not matches:
        return None
    path = matches[0].resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("clob_id") != raw_id:
        return None
    return payload


def clob_text(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    inner = payload.get("payload")
    if not isinstance(inner, dict):
        return ""
    return str(inner.get("text") or "")


def excerpt_text(text: str, *, max_chars: int = 1800) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    head_chars = max(200, max_chars // 2)
    tail_chars = max(200, max_chars - head_chars - 80)
    head = raw[:head_chars].rstrip()
    tail = raw[-tail_chars:].lstrip()
    return f"{head}\n... [middle omitted from compact clob context; total_chars={len(raw)}]\n{tail}"


def _terminal_summary_lines(result: dict[str, Any]) -> list[str]:
    lines = [
        "Terminal result from an explicitly executed text-console mount.",
        f"$ {result.get('command') or ''}",
    ]
    target_bits = [
        str(result.get("target_display_name") or result.get("target_id") or "").strip(),
        str(result.get("target_os") or "").strip(),
        str(result.get("target_shell") or "").strip(),
    ]
    target_bits = [item for item in target_bits if item]
    if target_bits:
        lines.append(f"target: {' · '.join(target_bits)}")
    lines.append(f"cwd: {result.get('cwd') or '.'}")
    exit_code = result.get("exit_code")
    lines.append(f"exit: {'timeout' if exit_code is None else exit_code}")
    return lines


def build_terminal_result_model_context(
    result: dict[str, Any],
    *,
    clobs: list[dict[str, Any]],
    inline_excerpt_chars: int = 1800,
) -> dict[str, Any]:
    lines = _terminal_summary_lines(result)
    lines.append("")
    if clobs:
        lines.append("Large terminal output was saved as side-loaded clob(s).")
        lines.append("Use targeted clob lookup evidence for follow-up questions; do not assume the full output is pasted here.")
        lines.append("")
    clob_by_stream = {str(item.get("stream") or ""): item for item in clobs}
    for stream_name in ("stdout", "stderr"):
        stream_text = str(result.get(stream_name) or "")
        clob = clob_by_stream.get(stream_name)
        if clob:
            lines.append(f"{stream_name}: side-loaded clob reference")
            lines.append(f"- clob_id: {clob.get('clob_id')}")
            lines.append(f"- clob_type: {clob.get('clob_type')}")
            lines.append(f"- payload_sha256: {clob.get('payload_sha256')}")
            lines.append(f"- text_chars: {clob.get('text_chars')}")
            lines.append(f"- line_count: {clob.get('line_count')}")
            excerpt = excerpt_text(stream_text, max_chars=inline_excerpt_chars).strip()
            if excerpt:
                lines.append(f"- compact_excerpt:\n{excerpt}")
            lines.append("")
        elif stream_text.strip():
            lines.append(f"{stream_name}:\n{stream_text.strip()}")
            lines.append("")
    error = str(result.get("error") or "").strip()
    if error:
        lines.append(f"error:\n{error}")
    thread_text = "\n".join(lines).strip()
    return {
        "kind": "terminal_result_clob_context",
        "thread_text": thread_text,
        "clob_count": len(clobs),
        "clob_ids": [str(item.get("clob_id") or "") for item in clobs if item.get("clob_id")],
        "thread_text_chars": len(thread_text),
    }


def enrich_terminal_result_with_clobs(
    repo_root: str | Path,
    result: dict[str, Any],
    *,
    threshold_chars: int = DEFAULT_TERMINAL_CLOB_THRESHOLD_CHARS,
    inline_excerpt_chars: int = 1800,
) -> dict[str, Any]:
    """Attach clob metadata/model context to a Terminal run result when output is large."""

    enriched = dict(result)
    clobs: list[dict[str, Any]] = []
    for stream_name in ("stdout", "stderr"):
        stream_text = str(enriched.get(stream_name) or "")
        if len(stream_text) < threshold_chars:
            continue
        clob = save_text_console_clob(
            repo_root,
            clob_type="terminal_output",
            text=stream_text,
            source={
                "surface": "text_console",
                "kind": "terminal_result",
                "stream": stream_name,
                "command": str(enriched.get("command") or ""),
                "cwd": str(enriched.get("cwd") or ""),
                "target_id": str(enriched.get("target_id") or ""),
                "exit_code": enriched.get("exit_code"),
            },
            metadata={
                "stream": stream_name,
                "timed_out": bool(enriched.get("timed_out")),
                "duration_ms": enriched.get("duration_ms"),
            },
        )
        clobs.append({"stream": stream_name, **clob})

    if clobs:
        enriched["text_console_clobs"] = clobs
        enriched["model_context"] = build_terminal_result_model_context(
            enriched,
            clobs=clobs,
            inline_excerpt_chars=inline_excerpt_chars,
        )
    return enriched


def extract_clob_ids_from_text(text: str) -> list[str]:
    seen: list[str] = []
    for match in CLOB_ID_RE.finditer(str(text or "")):
        clob_id = match.group(0)
        if clob_id not in seen:
            seen.append(clob_id)
    return seen


def extract_clob_ids_from_thread_messages(messages: list[Any]) -> list[str]:
    seen: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        else:
            content = str(getattr(message, "content", "") or "")
        for clob_id in extract_clob_ids_from_text(content):
            if clob_id not in seen:
                seen.append(clob_id)
    return seen


def query_terms_from_prompt(prompt: str, *, max_terms: int = 10) -> list[str]:
    terms: list[str] = []
    for token in WORD_RE.findall(str(prompt or "").lower()):
        candidates = [token]
        if "_" in token:
            candidates.extend(part for part in token.split("_") if part)
        if "/" in token:
            candidates.extend(part for part in token.split("/") if part)
        if "-" in token:
            candidates.extend(part for part in token.split("-") if part)
        for candidate in candidates:
            candidate = candidate.strip("._-/")
            if len(candidate) < 3:
                continue
            if candidate in STOPWORDS:
                continue
            if candidate.startswith("clob-"):
                continue
            if candidate not in terms:
                terms.append(candidate)
            if len(terms) >= max_terms:
                return terms
    return terms


def lookup_text_console_clob_lines(
    payload: dict[str, Any],
    *,
    terms: list[str],
    max_results: int = 12,
) -> list[TextConsoleClobLookupResult]:
    clob_id = str(payload.get("clob_id") or "")
    text = clob_text(payload)
    term_list = [term.lower() for term in terms if term]
    results: list[TextConsoleClobLookupResult] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if term_list:
            score = sum(1 for term in term_list if term in lower)
            if score <= 0:
                continue
        else:
            score = 1
        results.append(TextConsoleClobLookupResult(clob_id=clob_id, line_number=line_number, text=stripped, score=score))
    results.sort(key=lambda item: (-item.score, item.line_number, item.text))
    return results[:max_results]


def response_uses_text_console_clob_evidence(
    response_text: str,
    lookup_metadata: dict[str, Any] | None,
    *,
    min_exact_text_chars: int = 12,
) -> dict[str, Any]:
    """Report whether a model response cites retrieved clob lookup evidence.

    This is intentionally a narrow grounding check: a response passes by naming
    a runtime evidence_id or by copying an exact retrieved evidence line.  It
    does not prove semantic correctness; it flags whether the answer was tied to
    the bounded side-loaded clob slice instead of unsupported memory.
    """

    metadata = lookup_metadata if isinstance(lookup_metadata, dict) else {}
    response = str(response_text or "")
    evidence_items = [item for item in list(metadata.get("evidence", []) or []) if isinstance(item, dict)]
    matched_ids: list[str] = []
    matched_texts: list[str] = []
    for item in evidence_items:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if evidence_id and evidence_id in response and evidence_id not in matched_ids:
            matched_ids.append(evidence_id)
        text = str(item.get("text") or "").strip()
        if len(text) >= min_exact_text_chars and text in response and text not in matched_texts:
            matched_texts.append(text)

    result_count = int(metadata.get("result_count") or len(evidence_items) or 0)
    return {
        "ok": bool(matched_ids or matched_texts),
        "result_count": result_count,
        "matched_ids": matched_ids,
        "matched_texts": matched_texts,
        "evidence_ids": [str(item.get("evidence_id") or "") for item in evidence_items if item.get("evidence_id")],
        "requires_grounding": result_count > 0,
    }


def build_text_console_clob_lookup_context(
    repo_root: str | Path,
    *,
    prompt: str,
    thread_messages: list[Any],
    max_chars: int = DEFAULT_CLOB_LOOKUP_MAX_CHARS,
    max_results_per_clob: int = 12,
) -> tuple[str, dict[str, Any]]:
    """Return bounded RAG evidence looked up from clobs referenced in thread context."""

    clob_ids = extract_clob_ids_from_thread_messages(thread_messages)
    terms = query_terms_from_prompt(prompt)
    all_results: list[dict[str, Any]] = []
    loaded_ids: list[str] = []
    for clob_id in clob_ids:
        payload = load_text_console_clob(repo_root, clob_id)
        if not payload:
            continue
        loaded_ids.append(clob_id)
        for result in lookup_text_console_clob_lines(payload, terms=terms, max_results=max_results_per_clob):
            all_results.append(result.to_payload(f"clob-evidence-{len(all_results) + 1:03d}"))

    lines: list[str] = []
    if all_results:
        lines.extend(
            [
                "Side-loaded text-console clob lookup evidence.",
                "This evidence was retrieved from saved clob payloads referenced by earlier thread messages.",
                "Use only these bounded lookup lines as evidence; the full clob payload is not pasted into the model context.",
                "Grounding requirement: use evidence_id values internally for verification, but do not print evidence_id, clob_id, or clob-evidence-* labels; quote exact retrieved text or name the relevant file/path/line normally.",
                f"query_terms: {', '.join(terms) if terms else '[none]'}",
                "",
                "grounding_evidence:",
            ]
        )
        for item in all_results:
            candidate = (
                f"- evidence_id={item['evidence_id']} clob_id={item['clob_id']} "
                f"line={item['line_number']} score={item['score']} text={item['text']}"
            )
            if len("\n".join([*lines, candidate])) > max_chars:
                lines.append(f"- ... [clob lookup evidence truncated at {max_chars} chars]")
                break
            lines.append(candidate)

    context = "\n".join(lines).strip()
    metadata = {
        "clob_ids_seen": clob_ids,
        "clob_ids_loaded": loaded_ids,
        "query_terms": terms,
        "result_count": len(all_results),
        "context_chars": len(context),
        "max_chars": max_chars,
        "full_clob_injected": False,
        "grounding_required": bool(all_results),
        "evidence_ids": [str(item.get("evidence_id") or "") for item in all_results if item.get("evidence_id")],
        "evidence": all_results,
    }
    return context, metadata
