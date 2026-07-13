from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any

from main_computer.main_log_codec import canonical_json_line


_TEXT_FIELDS = (
    "message",
    "msg",
    "text",
    "line",
    "log",
    "event",
    "detail",
    "details",
    "error",
    "stderr",
    "stdout",
    "summary",
    "status",
    "status_preview",
    "chunk",
)
_CONTEXT_FIELDS = (
    "level",
    "severity",
    "kind",
    "service",
    "source_service",
    "component",
    "subsystem",
    "stream",
    "process_name",
)
_OPERATIONAL_NUMERIC_KEYS = {
    "code",
    "duration",
    "duration_ms",
    "elapsed",
    "elapsed_ms",
    "exit_code",
    "latency",
    "latency_ms",
    "returncode",
    "retries",
    "retry",
    "status",
    "status_code",
    "wait_ms",
}
_RANDOM_ID_KEYS = {
    "id",
    "uuid",
    "guid",
    "hash",
    "nonce",
    "token",
    "trace",
    "trace_id",
    "request_id",
    "req_id",
    "correlation_id",
    "session_id",
    "span_id",
}
# These fields are pathway-defining.  The first compressor version applied the
# generic high-entropy token rule to them, which turned stable values such as
# ``main-computer-applications-service`` and ``app_control.py`` into
# ``<random_string>``.  That collapsed unrelated behavior profiles before MDS
# or NMDS ever saw them.
_SEMANTIC_VALUE_KEYS = set(_CONTEXT_FIELDS) | {
    "child",
    "component",
    "subsystem",
    "mode",
    "state",
    "phase",
    "reason",
    "method",
    "route",
    "path",
    "endpoint",
    "operation",
    "action",
    "command_name",
}


BITCOUNT = tuple(bin(i).count("1") for i in range(256))


_KEY_VALUE_RE = re.compile(
    r"\b(?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)\s*=\s*(?P<value>[^\s,;]+)"
)
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_DATE_RE = re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HASH_RE = re.compile(r"\b(?:sha256:)?[0-9a-fA-F]{32,128}\b")
_URL_RE = re.compile(r"\bhttps?://\S+\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"']+")
_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?:[^\s\"']+/)*[^\s\"']+")
_LONG_DIGIT_RE = re.compile(r"\b\d{8,}\b")
_FLOAT_RE = re.compile(
    r"(?<![A-Za-z0-9_=])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![A-Za-z0-9_])"
)
_QUOTED_RANDOM_RE = re.compile(r"""(["'])(?P<value>[A-Za-z0-9_./+=:-]{12,})\1""")
_HIGH_ENTROPY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_./+=:-]{20,}\b")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LogSurpriseConfig:
    alpha: float = 0.5
    max_signatures: int = 1000
    max_surprise_events: int = 200
    max_boring_runs: int = 200
    histogram_bins: int = 12
    boring_surprise_bits: float = 1.0
    tuning_entropy_threshold: float = 0.98
    preview_chars: int = 240
    sidecar_flush_min_events: int = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: str | bytes, *, prefix: bool = True, chars: int = 16) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    digest = hashlib.sha256(value).hexdigest()[:chars]
    return f"sha256:{digest}" if prefix else digest


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def bit_entropy(data: bytes) -> float:
    """Return empirical zero/one entropy in bits per bit for a byte string.

    A value near 1.0 means the observed bits are close to balanced. That can be
    a useful tuning signal only after volatile fields have been discounted.
    """

    total = len(data) * 8
    if total <= 0:
        return 0.0
    ones = sum(BITCOUNT[b] for b in data)
    zeros = total - ones
    entropy = 0.0
    for count in (zeros, ones):
        if count:
            probability = count / total
            entropy -= probability * math.log2(probability)
    return entropy


def _wordy_stable_token(text: str) -> bool:
    """Return True for stable operational names that merely look entropic.

    Hyphenated service names and dotted process names are common in this repo:
    main-computer-applications-service, app_control.py, executor_service.py.
    They have enough character classes to trip a naive entropy heuristic, but
    they are exactly the values that define log-derived execution pathways.
    """

    if len(text) > 120:
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[-_.:][A-Za-z][A-Za-z0-9]*){0,12}", text):
        return False
    digit_fraction = sum(c.isdigit() for c in text) / max(1, len(text))
    return digit_fraction <= 0.25


def _looks_random_token(value: str) -> bool:
    text = value.strip().strip('"\'')
    if len(text) < 12:
        return False
    if _wordy_stable_token(text):
        return False
    classes = sum(
        bool(check(text))
        for check in (
            lambda s: any(c.islower() for c in s),
            lambda s: any(c.isupper() for c in s),
            lambda s: any(c.isdigit() for c in s),
            lambda s: any(not c.isalnum() for c in s),
        )
    )
    unique_fraction = len(set(text)) / max(1, len(text))
    return classes >= 2 and unique_fraction >= 0.45


def _route_token(raw: str) -> str | None:
    value = raw.strip().strip('"\'')
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        try:
            # Avoid importing urllib for a tiny hot-path; split enough to keep
            # just the path shape.
            value = "/" + value.split("/", 3)[3].split("?", 1)[0].split("#", 1)[0]
        except Exception:
            return None
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        return None
    if not value.startswith("/"):
        return None
    pieces: list[str] = []
    for part in value.split("/"):
        if not part:
            continue
        low = part.lower()
        if _UUID_RE.fullmatch(low):
            pieces.append("uuid")
        elif _HASH_RE.fullmatch(low):
            pieces.append("hash")
        elif _LONG_DIGIT_RE.fullmatch(low) or re.fullmatch(r"\d+", low):
            pieces.append("num")
        elif _looks_random_token(low):
            pieces.append("id")
        elif re.fullmatch(r"[a-z0-9_.:-]{1,48}", low):
            pieces.append(low)
        else:
            pieces.append("value")
    if not pieces:
        return None
    return "<route:" + ".".join(pieces[:12]) + ">"


def _semantic_value(key: str, value: str) -> str:
    key_l = key.lower().replace("-", "_")
    stripped = str(value).strip().strip('"\'').rstrip(".")
    if not stripped:
        return "<empty>"
    if key_l in {"path", "route", "endpoint"}:
        route = _route_token(stripped)
        if route is not None:
            return route
        return "<path>"
    if _TIMESTAMP_RE.fullmatch(stripped):
        return "<ts>"
    if _DATE_RE.fullmatch(stripped):
        return "<date>"
    if _TIME_RE.fullmatch(stripped):
        return "<time>"
    if _UUID_RE.fullmatch(stripped):
        return "<uuid>"
    if _HASH_RE.fullmatch(stripped):
        return "<hash>"
    if _LONG_DIGIT_RE.fullmatch(stripped):
        return "<random_number_string>"
    if _FLOAT_RE.fullmatch(stripped):
        return "<num>"
    # Preserve stable service/process/state/method names even when their
    # punctuation would look random to the generic entropy rule.
    if _wordy_stable_token(stripped) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", stripped):
        return stripped.lower()
    if _looks_random_token(stripped):
        return "<random_string>"
    return normalize_log_text(stripped)


def _bucket_numeric_value(key: str, raw_value: str) -> str:
    key_l = key.lower().replace("-", "_")
    stripped = raw_value.strip().strip('"\'')
    try:
        value = float(stripped)
    except ValueError:
        return "<num>"

    if key_l in {"status", "status_code", "code", "exit_code", "returncode", "retry", "retries"}:
        return stripped

    if "duration" in key_l or "latency" in key_l or key_l.endswith("_ms") or key_l in {"elapsed", "wait_ms"}:
        if value < 100:
            return "<latency:fast>"
        if value < 1000:
            return "<latency:normal>"
        if value < 5000:
            return "<latency:slow>"
        return "<latency:very_slow>"

    if "byte" in key_l or "size" in key_l:
        if value < 1024:
            return "<size:tiny>"
        if value < 1024 * 1024:
            return "<size:small>"
        if value < 1024 * 1024 * 1024:
            return "<size:large>"
        return "<size:huge>"

    return "<num>"


def _normalize_key_value(match: re.Match[str]) -> str:
    key = match.group("key")
    value = match.group("value")
    key_l = key.lower().replace("-", "_")
    stripped = value.strip().strip('"\'').rstrip(".")

    if key_l in _RANDOM_ID_KEYS or key_l.endswith("_id") or key_l.endswith("id"):
        return f"{key}=<{key_l}>"

    if key_l in _OPERATIONAL_NUMERIC_KEYS or any(part in key_l for part in ("duration", "latency", "elapsed", "bytes", "size")):
        if _FLOAT_RE.fullmatch(stripped):
            return f"{key}={_bucket_numeric_value(key_l, stripped)}"

    if key_l in _SEMANTIC_VALUE_KEYS:
        return f"{key}={_semantic_value(key_l, value)}"

    if _TIMESTAMP_RE.fullmatch(stripped):
        return f"{key}=<ts>"
    if _DATE_RE.fullmatch(stripped):
        return f"{key}=<date>"
    if _TIME_RE.fullmatch(stripped):
        return f"{key}=<time>"
    if _UUID_RE.fullmatch(stripped):
        return f"{key}=<uuid>"
    if _HASH_RE.fullmatch(stripped):
        return f"{key}=<hash>"
    if _LONG_DIGIT_RE.fullmatch(stripped):
        return f"{key}=<random_number_string>"
    if _looks_random_token(stripped):
        return f"{key}=<random_string>"
    if _FLOAT_RE.fullmatch(stripped):
        return f"{key}=<num>"
    return f"{key}={value}"


def normalize_log_text(text: object) -> str:
    """Normalize volatile fields without claiming full semantic knowledge."""

    out = str(text or "").strip()
    if not out:
        return "<empty>"

    out = _KEY_VALUE_RE.sub(_normalize_key_value, out)
    out = _TIMESTAMP_RE.sub("<ts>", out)
    out = _DATE_RE.sub("<date>", out)
    out = _TIME_RE.sub("<time>", out)
    out = _UUID_RE.sub("<uuid>", out)
    out = _HASH_RE.sub("<hash>", out)
    out = _URL_RE.sub("<url>", out)
    out = _EMAIL_RE.sub("<email>", out)
    out = _IP_RE.sub("<ip>", out)
    out = _WINDOWS_PATH_RE.sub("<path>", out)
    out = _POSIX_PATH_RE.sub("<path>", out)
    out = _LONG_DIGIT_RE.sub("<random_number_string>", out)

    def _quoted(match: re.Match[str]) -> str:
        return '"<random_string>"' if _looks_random_token(match.group("value")) else match.group(0)

    out = _QUOTED_RANDOM_RE.sub(_quoted, out)

    def _token(match: re.Match[str]) -> str:
        value = match.group(0)
        if "=" in value:
            key, _, _raw = value.partition("=")
            key_l = key.lower().replace("-", "_")
            if key_l in _SEMANTIC_VALUE_KEYS or key_l in _OPERATIONAL_NUMERIC_KEYS or key_l in _RANDOM_ID_KEYS:
                return value
        return "<random_string>" if _looks_random_token(value) else value

    out = _HIGH_ENTROPY_TOKEN_RE.sub(_token, out)
    out = _FLOAT_RE.sub("<num>", out)
    out = _WHITESPACE_RE.sub(" ", out).strip()
    return out[:1000] if out else "<empty>"


def _record_text(record: dict[str, Any]) -> str:
    chunks: list[str] = []
    for field in _TEXT_FIELDS:
        value = record.get(field)
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            chunks.append(f"{field}={json.dumps(value, sort_keys=True, default=str)}")
        else:
            chunks.append(f"{field}={value}")
    if chunks:
        return " | ".join(chunks)
    return canonical_json_line(record)


def signature_for_event(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in _CONTEXT_FIELDS:
        value = event.get(field)
        if value not in (None, ""):
            parts.append(f"{field}={_semantic_value(field, str(value))}")
    parts.append(normalize_log_text(_record_text(event)))
    return " | ".join(parts)


def _histogram(values: list[float], bins: int) -> list[dict[str, Any]]:
    clean = [value for value in values if math.isfinite(value)]
    if not clean:
        return []
    lo = min(clean)
    hi = max(clean)
    if lo == hi:
        return [{"min": round(lo, 6), "max": round(hi, 6), "count": len(clean), "bar": "#" * min(40, len(clean))}]
    bin_count = max(1, int(bins))
    width = (hi - lo) / bin_count
    counts = [0] * bin_count
    for value in clean:
        index = min(bin_count - 1, int((value - lo) / width))
        counts[index] += 1
    max_count = max(counts) or 1
    out: list[dict[str, Any]] = []
    for index, count in enumerate(counts):
        bar_width = round((count / max_count) * 40) if count else 0
        out.append(
            {
                "min": round(lo + index * width, 6),
                "max": round(lo + (index + 1) * width, 6),
                "count": count,
                "bar": "#" * bar_width,
            }
        )
    return out


class LogSurpriseCompressor:
    """Bounded online compressor for model-relative log surprise.

    The compressor intentionally avoids full log-dialect claims. It learns stable
    signatures after volatile values are normalized, collapses low-surprise runs,
    and keeps rare events for realtime display.
    """

    def __init__(self, config: LogSurpriseConfig | None = None) -> None:
        self.config = config or LogSurpriseConfig()
        self._lock = threading.RLock()
        self._counts: Counter[str] = Counter()
        self._signature_ids: dict[str, str] = {}
        self._signature_meta: dict[str, dict[str, Any]] = {}
        self._surprise_events: list[dict[str, Any]] = []
        self._boring_runs: deque[dict[str, Any]] = deque(maxlen=self.config.max_boring_runs)
        self._surprise_values: deque[float] = deque(maxlen=max(1, self.config.max_surprise_events * 10))
        self._line_entropy_values: deque[float] = deque(maxlen=max(1, self.config.max_surprise_events * 10))
        self._total_events = 0
        self._raw_bytes_seen = 0
        self._flush_generation = 0

    def observe(self, event: dict[str, Any]) -> dict[str, Any]:
        raw_line = canonical_json_line(event)
        raw_bytes = raw_line.encode("utf-8", "replace")
        sig = signature_for_event(event)
        sig_preview = sig[: self.config.preview_chars]
        entropy = bit_entropy(raw_bytes)

        with self._lock:
            prior_total = self._total_events
            prior_count = self._counts[sig]
            signature_id = self._signature_ids.get(sig)
            if signature_id is None:
                if len(self._signature_ids) >= self.config.max_signatures:
                    sig = "<overflow_signature>"
                    sig_preview = sig
                    prior_count = self._counts[sig]
                    signature_id = self._signature_ids.get(sig)
                if signature_id is None:
                    signature_id = f"S{len(self._signature_ids)}"
                    self._signature_ids[sig] = signature_id
                    self._signature_meta[signature_id] = {
                        "signature_id": signature_id,
                        "template": sig_preview,
                        "signature_hash": _sha(sig),
                        "count": 0,
                        "first_seq": self._total_events + 1,
                        "last_seq": self._total_events + 1,
                        "max_surprise_bits": 0.0,
                        "last_surprise_bits": 0.0,
                        "example_raw_hash": _sha(raw_bytes),
                    }

            if prior_total == 0:
                probability = 1.0
                surprise = 0.0
            else:
                vocabulary = len(self._signature_ids)
                denominator = prior_total + self.config.alpha * (vocabulary + 1)
                probability = (prior_count + self.config.alpha) / denominator
                surprise = -math.log2(probability)

            self._total_events += 1
            seq = self._total_events
            self._raw_bytes_seen += len(raw_bytes)
            self._counts[sig] += 1
            self._surprise_values.append(surprise)
            self._line_entropy_values.append(entropy)

            meta = self._signature_meta[signature_id]
            meta["count"] = self._counts[sig]
            meta["last_seq"] = seq
            meta["last_surprise_bits"] = round(surprise, 6)
            meta["max_surprise_bits"] = round(max(float(meta.get("max_surprise_bits") or 0.0), surprise), 6)
            meta["last_seen_at"] = _now_iso()

            record = {
                "seq": seq,
                "signature_id": signature_id,
                "signature_hash": meta["signature_hash"],
                "signature_preview": sig_preview,
                "prior_count": prior_count,
                "prior_total": prior_total,
                "probability_estimate": round(probability, 12),
                "surprise_bits": round(surprise, 6),
                "line_entropy_bits_per_bit": round(entropy, 6),
                "raw_hash": _sha(raw_bytes),
                "raw_bytes": len(raw_bytes),
                "preview": raw_line[: self.config.preview_chars],
            }

            if surprise <= self.config.boring_surprise_bits and prior_count > 0:
                if self._boring_runs and self._boring_runs[-1].get("signature_id") == signature_id:
                    self._boring_runs[-1]["count"] = int(self._boring_runs[-1]["count"]) + 1
                    self._boring_runs[-1]["last_seq"] = seq
                else:
                    self._boring_runs.append(
                        {
                            "signature_id": signature_id,
                            "count": 1,
                            "first_seq": seq,
                            "last_seq": seq,
                        }
                    )
            else:
                self._surprise_events.append(record)
                self._surprise_events.sort(key=lambda item: (float(item.get("surprise_bits") or 0.0), int(item.get("seq") or 0)), reverse=True)
                del self._surprise_events[self.config.max_surprise_events :]

            self._flush_generation += 1
            return record

    def snapshot(self, *, limit: int = 200) -> dict[str, Any]:
        with self._lock:
            limit = max(1, int(limit))
            signatures = sorted(
                self._signature_meta.values(),
                key=lambda item: (int(item.get("count") or 0), float(item.get("max_surprise_bits") or 0.0)),
                reverse=True,
            )
            dominant_count = int(signatures[0]["count"]) if signatures else 0
            raw_bytes_seen = self._raw_bytes_seen
            top_surprise = self._surprise_events[:limit]
            surprise_values = list(self._surprise_values)
            entropy_values = list(self._line_entropy_values)
            summary_without_size = {
                "schema_version": 1,
                "ok": True,
                "mode": "semantic_surprise_summary",
                "updated_at": _now_iso(),
                "warning": "Scores are model-relative estimates from normalized signatures, not proof of semantic meaning.",
                "model": {
                    "event_unit": "normalized_log_signature",
                    "surprise": "-log2(P(signature | prior stream))",
                    "mathematical_log_base": 2,
                    "volatile_fields_discounted": True,
                    "random_payloads_do_not_create_surprise": True,
                },
                "summary": {
                    "total_events": self._total_events,
                    "unique_signatures": len(self._signature_ids),
                    "raw_bytes_seen": raw_bytes_seen,
                    "dominant_signature_fraction": round(dominant_count / self._total_events, 6) if self._total_events else 0.0,
                    "surprise_events_retained": len(self._surprise_events),
                    "boring_runs_retained": len(self._boring_runs),
                    "max_surprise_bits": _round(max(surprise_values) if surprise_values else 0.0),
                    "mean_recent_surprise_bits": _round(sum(surprise_values) / len(surprise_values) if surprise_values else 0.0),
                    "mean_recent_line_entropy_bits_per_bit": _round(sum(entropy_values) / len(entropy_values) if entropy_values else 0.0),
                },
                "histograms": {
                    "surprise_bits": _histogram(surprise_values, self.config.histogram_bins),
                    "line_entropy_bits_per_bit": _histogram(entropy_values, self.config.histogram_bins),
                },
                "signatures": signatures[:limit],
                "boring_runs": list(self._boring_runs)[-limit:],
                "top_surprise_events": top_surprise,
            }
            encoded = json.dumps(summary_without_size, sort_keys=True, default=str).encode("utf-8")
            compression_ratio = round(len(encoded) / raw_bytes_seen, 6) if raw_bytes_seen else 0.0
            summary_without_size["compression"] = {
                "sidecar_snapshot_bytes": len(encoded),
                "raw_bytes_seen": raw_bytes_seen,
                "sidecar_to_raw_ratio": compression_ratio,
                "note": "This is a semantic summary ratio, not lossless reconstruction size.",
            }
            return summary_without_size

    def write_snapshot(self, path: Path | str, *, limit: int = 200) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.snapshot(limit=limit)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        tmp.replace(target)

    def should_flush(self) -> bool:
        with self._lock:
            return self._flush_generation >= max(1, self.config.sidecar_flush_min_events)

    def mark_flushed(self) -> None:
        with self._lock:
            self._flush_generation = 0
