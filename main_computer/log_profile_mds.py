from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import random
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from main_computer.log_surprise_compressor import signature_for_event
from main_computer.main_log_pack import default_log_path, iter_main_log_records


SCHEMA_VERSION = 1
PROFILE_MAP_SCHEMA = "mclog-profile-map-v1"


_MESSAGE_KEY_VALUE_RE = re.compile(r"\b(?P<key>[A-Za-z_][A-Za-z0-9_.:-]*)\s*=\s*(?P<value>[^\s,;]+)")
_STABLE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$")
_VOLATILE_KEY_PARTS = (
    "id",
    "uuid",
    "guid",
    "hash",
    "token",
    "nonce",
    "trace",
    "span",
    "session",
    "request",
    "correlation",
    "path",
    "file",
    "pid",
    "time",
    "date",
    "at",
)
_SEMANTIC_KEYS = {
    "state",
    "phase",
    "reason",
    "status",
    "status_code",
    "exit_code",
    "returncode",
    "method",
    "kind",
    "stream",
    "service",
    "source_service",
    "component",
    "subsystem",
    "mode",
    "health",
    "ready",
    "attempt",
    "retries",
    "retry",
}
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rule.traceback", ("traceback", "most recent call last")),
    ("rule.python_exception", ("exception",)),
    ("rule.error", ("error", "failed", "failure")),
    ("rule.warning", ("warn", "warning")),
    ("rule.boot_started", ("starting", "boot")),
    ("rule.boot_complete", ("boot complete", "initial boot complete")),
    ("rule.boot_retry", ("boot retry", "retrying")),
    ("rule.health", ("health", "healthy")),
    ("rule.http_request", ("http-request", "http-log", "GET ", "POST ", "PUT ", "DELETE ")),
    ("rule.docker", ("docker", "podman", "compose")),
    ("rule.coolify", ("coolify",)),
    ("rule.executor", ("executor",)),
    ("rule.applications", ("applications",)),
    ("rule.blockchain", ("blockchain",)),
    ("rule.skipped", ("skipped",)),
    ("rule.ready", ("ready",)),
)


@dataclass(frozen=True)
class ProfileMapOptions:
    window: str = "information"
    target_surprise_bits: float = 512.0
    stride_surprise_bits: float = 512.0
    event_window: int = 500
    event_stride: int = 500
    seconds_window: float = 60.0
    seconds_stride: float = 60.0
    max_coverage_points: int = 10_000
    max_profiles: int = 300
    alpha: float = 0.5
    normalize: str = "log1p_l1"
    distance: str = "manhattan"
    feature_weighting: str = "tfidf"
    min_df: int = 1
    max_df_fraction: float = 0.95
    embedding: str = "pca"
    dimensions: int = 2
    include_distance_matrix: bool = False
    nmds_iterations: int = 80
    nmds_restarts: int = 3
    nmds_seed: int = 17


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str, *, chars: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:chars]


def _cp_id(key: str) -> str:
    return "cp_" + _sha(key, chars=12)


def _sig_hash(signature: str) -> str:
    return "sha256:" + _sha(signature, chars=16)


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _record_time_seconds(record: dict[str, Any]) -> float | None:
    for key in ("at", "received_at"):
        value = record.get(key)
        if not isinstance(value, str) or not value:
            continue
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _message_text(record: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("message", "msg", "text", "line", "event", "detail", "error", "stderr", "stdout", "summary", "status", "chunk"):
        value = record.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            chunks.append(value)
        else:
            chunks.append(json.dumps(value, sort_keys=True, default=str))
    return " | ".join(chunks)


def _value_bucket(key: str, value: str) -> str | None:
    key_l = key.lower()
    value_s = str(value).strip().strip("\"'")
    value_l = value_s.lower()

    if any(part in key_l for part in _VOLATILE_KEY_PARTS) and key_l not in _SEMANTIC_KEYS:
        return None
    if not value_s:
        return None
    if len(value_s) > 120:
        return None

    if key_l in {"status", "status_code", "exit_code", "returncode"}:
        m = re.search(r"-?\d+", value_s)
        if m:
            return m.group(0)

    if key_l in {"duration_ms", "latency_ms", "elapsed_ms", "wait_ms"}:
        m = re.search(r"\d+", value_s)
        if not m:
            return None
        n = int(m.group(0))
        if n < 100:
            return "lt100"
        if n < 1000:
            return "100_999"
        if n < 5000:
            return "1000_4999"
        return "gte5000"

    if key_l in _SEMANTIC_KEYS or _STABLE_TOKEN_RE.match(value_s):
        # Normalize huge numeric counters into presence buckets. They are useful,
        # but should not create unlimited coverage columns.
        if re.fullmatch(r"\d{4,}", value_s):
            return "large_number"
        return value_l[:80]

    return None


class CoverageDictionary:
    def __init__(self, max_points: int) -> None:
        self.max_points = max(1, int(max_points))
        self.points: dict[str, dict[str, Any]] = {}
        self.key_to_id: dict[str, str] = {}
        self.truncated_new_points = 0

    def add(self, key: str, *, label: str, kind: str, parent_key: str | None = None) -> str | None:
        existing = self.key_to_id.get(key)
        if existing:
            return existing
        if len(self.key_to_id) >= self.max_points:
            self.truncated_new_points += 1
            return None
        cp = _cp_id(key)
        # Resolve rare hash collision by extending deterministically.
        if cp in self.points:
            cp = "cp_" + _sha(key + "|collision", chars=16)
        parent = self.key_to_id.get(parent_key) if parent_key else None
        self.key_to_id[key] = cp
        self.points[cp] = {
            "id": cp,
            "key": key,
            "type": kind,
            "label": label,
        }
        if parent:
            self.points[cp]["parent"] = parent
        return cp


def _event_coverage_points(
    record: dict[str, Any],
    *,
    signature_hash: str,
    signature_preview: str,
    previous_signature_hash: str | None,
    dictionary: CoverageDictionary,
) -> dict[str, list[str]]:
    positive: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    def hit(key: str, label: str, kind: str = "semantic", *, bucket: str = "positive", parent_key: str | None = None) -> None:
        cp = dictionary.add(key, label=label, kind=kind, parent_key=parent_key)
        if not cp:
            return
        if bucket == "skipped":
            skipped.append(cp)
        elif bucket == "failed":
            failed.append(cp)
        else:
            positive.append(cp)

    hit(f"signature:{signature_hash}", f"signature {signature_preview[:160]}", "signature")
    for field in ("service", "source_service", "kind", "stream", "process_name"):
        value = record.get(field)
        if value not in (None, ""):
            cleaned = str(value).strip().lower()[:100]
            if cleaned:
                hit(f"field:{field}={cleaned}", f"{field}={cleaned}", "field", parent_key=f"signature:{signature_hash}")

    if previous_signature_hash:
        hit(
            f"transition:{previous_signature_hash}->{signature_hash}",
            f"transition {previous_signature_hash[-12:]} -> {signature_hash[-12:]}",
            "transition",
        )

    message = _message_text(record)
    message_l = message.lower()
    for key, values in _RULES:
        if any(value.lower() in message_l for value in values):
            bucket = "skipped" if key == "rule.skipped" else ("failed" if key in {"rule.traceback", "rule.python_exception", "rule.error"} else "positive")
            hit(key, key.replace("rule.", ""), "rule", bucket=bucket)

    for match in _MESSAGE_KEY_VALUE_RE.finditer(message):
        key = match.group("key")
        bucketed = _value_bucket(key, match.group("value"))
        if bucketed is None:
            continue
        bucket = "skipped" if bucketed == "skipped" else ("failed" if bucketed in {"failed", "error", "down"} else "positive")
        hit(f"kv:{key.lower()}={bucketed}", f"{key.lower()}={bucketed}", "key_value", bucket=bucket)

    for key in ("returncode", "exit_code"):
        value = record.get(key)
        if value not in (None, ""):
            bucketed = _value_bucket(key, str(value))
            if bucketed is not None:
                bucket = "failed" if bucketed not in {"0"} else "positive"
                hit(f"record:{key}={bucketed}", f"{key}={bucketed}", "record_field", bucket=bucket)

    # Deduplicate while preserving order. A line may emit the same point through
    # structured fields and text rules.
    def uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                out.append(item)
                seen.add(item)
        return out

    return {"positive": uniq(positive), "skipped": uniq(skipped), "failed": uniq(failed)}


def _surprise_for_signature(signature: str, counts: Counter[str], total: int, alpha: float) -> tuple[float, float]:
    if total <= 0:
        return 1.0, 0.0
    denominator = total + alpha * (len(counts) + 1)
    probability = (counts[signature] + alpha) / denominator
    surprise = -math.log2(probability)
    return probability, surprise


def _iter_profile_events(records: Iterable[dict[str, Any]], *, alpha: float, dictionary: CoverageDictionary) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    previous_hash: str | None = None
    total = 0

    for fallback_seq, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        signature = signature_for_event(record)
        signature_hash = _sig_hash(signature)
        probability, surprise = _surprise_for_signature(signature, counts, total, alpha)
        hits = _event_coverage_points(
            record,
            signature_hash=signature_hash,
            signature_preview=signature,
            previous_signature_hash=previous_hash,
            dictionary=dictionary,
        )
        seq = record.get("ingest_seq")
        try:
            seq_i = int(seq)
        except Exception:
            seq_i = fallback_seq
        out.append(
            {
                "seq": seq_i,
                "time": _record_time_seconds(record),
                "signature_hash": signature_hash,
                "signature_preview": signature[:240],
                "surprise_bits": surprise,
                "probability_estimate": probability,
                "positive": hits["positive"],
                "skipped": hits["skipped"],
                "failed": hits["failed"],
            }
        )
        counts[signature] += 1
        total += 1
        previous_hash = signature_hash
    return out


def _slice_profile(events: list[dict[str, Any]], start: int, end: int, profile_id: str) -> dict[str, Any]:
    positive: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    surprise_total = 0.0
    for event in events[start:end]:
        surprise_total += float(event.get("surprise_bits") or 0.0)
        signatures[str(event.get("signature_hash") or "")] += 1
        positive.update(event.get("positive") or [])
        skipped.update(event.get("skipped") or [])
        failed.update(event.get("failed") or [])
    if end <= start:
        raise ValueError("empty profile slice")
    first = events[start]
    last = events[end - 1]
    return {
        "profile_id": profile_id,
        "seq_start": first["seq"],
        "seq_end": last["seq"],
        "time_start": first.get("time"),
        "time_end": last.get("time"),
        "event_count": end - start,
        "surprise_bits_total": _round(surprise_total),
        "nonzero_points": len(set(positive) | set(skipped) | set(failed)),
        "positive_counts": dict(sorted(positive.items())),
        "skip_counts": dict(sorted(skipped.items())),
        "failure_counts": dict(sorted(failed.items())),
        "dominant_signatures": [key for key, _ in signatures.most_common(5) if key],
    }


def _window_profiles(events: list[dict[str, Any]], options: ProfileMapOptions) -> list[dict[str, Any]]:
    if not events:
        return []
    profiles: list[dict[str, Any]] = []
    mode = options.window.lower().strip()

    def add(start: int, end: int) -> None:
        if start < end and len(profiles) < options.max_profiles:
            profiles.append(_slice_profile(events, start, end, f"P{len(profiles) + 1:06d}"))

    if mode == "events":
        width = max(1, int(options.event_window))
        stride = max(1, int(options.event_stride))
        start = 0
        while start < len(events) and len(profiles) < options.max_profiles:
            add(start, min(len(events), start + width))
            start += stride
    elif mode == "time":
        stamped = [(idx, event.get("time")) for idx, event in enumerate(events)]
        stamped = [(idx, float(ts)) for idx, ts in stamped if ts is not None and math.isfinite(float(ts))]
        if not stamped:
            # Fall back to event windows if the log does not carry parseable timestamps.
            return _window_profiles(events, ProfileMapOptions(**{**options.__dict__, "window": "events"}))
        width = max(0.001, float(options.seconds_window))
        stride = max(0.001, float(options.seconds_stride))
        first_ts = stamped[0][1]
        last_ts = stamped[-1][1]
        t = first_ts
        while t <= last_ts and len(profiles) < options.max_profiles:
            idxs = [idx for idx, ts in stamped if t <= ts < t + width]
            if idxs:
                add(min(idxs), max(idxs) + 1)
            t += stride
    else:
        # Information windows: close each profile when it accumulates about N
        # surprise bits. Stride is implemented by moving the next start by the
        # requested surprise mass, so overlapping informational windows are
        # possible without pretending every window is identical.
        target = max(0.001, float(options.target_surprise_bits))
        stride = max(0.001, float(options.stride_surprise_bits))
        surprises = [max(0.0, float(event.get("surprise_bits") or 0.0)) for event in events]
        prefix = [0.0]
        for value in surprises:
            prefix.append(prefix[-1] + value)
        start = 0
        start_mass = 0.0
        while start < len(events) and len(profiles) < options.max_profiles:
            end_mass = start_mass + target
            end = start + 1
            while end < len(events) and prefix[end] < end_mass:
                end += 1
            add(start, min(len(events), end))
            start_mass += stride
            while start < len(events) and prefix[start] < start_mass:
                start += 1

    return profiles


def _combined_counts(profile: dict[str, Any]) -> dict[str, float]:
    combined: dict[str, float] = {}
    for prefix, key in (("pos", "positive_counts"), ("skip", "skip_counts"), ("fail", "failure_counts")):
        for cp, count in (profile.get(key) or {}).items():
            combined[f"{prefix}:{cp}"] = float(count)
    return combined


def _normalize_counts(counts: dict[str, float], mode: str) -> dict[str, float]:
    mode = mode.lower().strip()
    if mode == "binary":
        return {key: 1.0 for key, value in counts.items() if value}
    if mode == "log1p":
        return {key: math.log1p(value) for key, value in counts.items() if value}
    if mode == "sqrt":
        return {key: math.sqrt(value) for key, value in counts.items() if value > 0}
    if mode == "l1":
        total = sum(abs(value) for value in counts.values())
        return {key: value / total for key, value in counts.items() if value} if total else {}
    if mode == "log1p_l1":
        logged = {key: math.log1p(value) for key, value in counts.items() if value}
        total = sum(abs(value) for value in logged.values())
        return {key: value / total for key, value in logged.items() if value} if total else {}
    return {key: value for key, value in counts.items() if value}


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    return {key: value / norm for key, value in vector.items() if value} if norm else {}


def _prepare_vectors(profiles: list[dict[str, Any]], options: ProfileMapOptions) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Build comparable profile vectors and filter features that hide shape.

    The first profile-map version fed every coverage count straight into
    Manhattan distance.  That is faithful but often visually one-dimensional:
    always-on fields and high-volume windows dominate the first MDS axis, while
    behavioral composition collapses into a strip.  This preparation step keeps
    the original Manhattan option, but adds explicit, queryable controls for
    common-feature filtering and IDF weighting.
    """

    base_vectors = [_normalize_counts(_combined_counts(profile), options.normalize) for profile in profiles]
    profile_count = len(base_vectors)
    if profile_count == 0:
        return [], {
            "normalize": options.normalize,
            "feature_weighting": options.feature_weighting,
            "features_seen": 0,
            "features_retained": 0,
            "features_dropped_rare": 0,
            "features_dropped_common": 0,
        }

    df: Counter[str] = Counter()
    for vector in base_vectors:
        for key, value in vector.items():
            if value:
                df[key] += 1

    min_df = max(1, int(options.min_df))
    max_df_fraction = max(0.0, min(1.0, float(options.max_df_fraction)))
    max_df = max(1, math.floor(profile_count * max_df_fraction)) if max_df_fraction > 0 else profile_count

    retained = {
        key
        for key, count in df.items()
        if count >= min_df and count <= max_df
    }
    dropped_rare = sum(1 for count in df.values() if count < min_df)
    dropped_common = sum(1 for count in df.values() if count > max_df)

    weighting = (options.feature_weighting or "none").strip().lower()
    if weighting not in {"none", "idf", "tfidf", "tfidf_l2"}:
        weighting = "none"
    idf = {
        key: math.log((1.0 + profile_count) / (1.0 + count)) + 1.0
        for key, count in df.items()
    }

    vectors: list[dict[str, float]] = []
    for vector in base_vectors:
        filtered = {key: value for key, value in vector.items() if key in retained and value}
        if weighting in {"idf", "tfidf", "tfidf_l2"}:
            filtered = {key: value * idf.get(key, 1.0) for key, value in filtered.items()}
        if weighting == "tfidf_l2":
            filtered = _l2_normalize(filtered)
        vectors.append(filtered)

    diagnostics = {
        "normalize": options.normalize,
        "feature_weighting": weighting,
        "min_df": min_df,
        "max_df_fraction": _round(max_df_fraction),
        "max_df": max_df,
        "profile_count": profile_count,
        "features_seen": len(df),
        "features_retained": len(retained),
        "features_dropped_rare": dropped_rare,
        "features_dropped_common": dropped_common,
        "note": "Filtering/weighting is applied before distance calculation; missing retained features are zeros.",
    }
    return vectors, diagnostics


def _manhattan(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    return sum(abs(a.get(key, 0.0) - b.get(key, 0.0)) for key in keys)


def _braycurtis(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    numerator = sum(abs(a.get(key, 0.0) - b.get(key, 0.0)) for key in keys)
    denominator = sum(abs(a.get(key, 0.0)) + abs(b.get(key, 0.0)) for key in keys)
    return numerator / denominator if denominator else 0.0


def _weighted_jaccard(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    numerator = 0.0
    denominator = 0.0
    for key in keys:
        av = max(0.0, a.get(key, 0.0))
        bv = max(0.0, b.get(key, 0.0))
        numerator += min(av, bv)
        denominator += max(av, bv)
    return 1.0 - (numerator / denominator) if denominator else 0.0


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) & set(b)
    numerator = sum(a[key] * b[key] for key in keys)
    an = math.sqrt(sum(value * value for value in a.values()))
    bn = math.sqrt(sum(value * value for value in b.values()))
    if not an and not bn:
        return 0.0
    if not an or not bn:
        return 1.0
    similarity = max(-1.0, min(1.0, numerator / (an * bn)))
    return 1.0 - similarity


def _distance(a: dict[str, float], b: dict[str, float], metric: str) -> float:
    metric = (metric or "manhattan").strip().lower()
    if metric == "braycurtis":
        return _braycurtis(a, b)
    if metric == "weighted_jaccard":
        return _weighted_jaccard(a, b)
    if metric == "cosine":
        return _cosine(a, b)
    return _manhattan(a, b)


def _distance_matrix(vectors: list[dict[str, float]], metric: str = "manhattan") -> list[list[float]]:
    n = len(vectors)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _distance(vectors[i], vectors[j], metric)
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def _distance_diagnostics(distance: list[list[float]]) -> dict[str, Any]:
    values: list[float] = []
    for i in range(len(distance)):
        for j in range(i + 1, len(distance)):
            values.append(distance[i][j])
    if not values:
        return {"pair_count": 0, "min": None, "mean": None, "max": None, "zero_fraction": None}
    zeros = sum(1 for value in values if abs(value) < 1e-12)
    return {
        "pair_count": len(values),
        "min": _round(min(values)),
        "mean": _round(sum(values) / len(values)),
        "max": _round(max(values)),
        "zero_fraction": _round(zeros / len(values)),
    }


def _jacobi_eigen_symmetric(matrix: list[list[float]], *, max_sweeps: int = 240, tolerance: float = 1e-10) -> tuple[list[float], list[list[float]]]:
    n = len(matrix)
    if n == 0:
        return [], []
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    for _ in range(max_sweeps):
        p = 0
        q = 1 if n > 1 else 0
        max_abs = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                value = abs(a[i][j])
                if value > max_abs:
                    max_abs = value
                    p, q = i, j
        if max_abs < tolerance or n == 1:
            break

        if abs(a[p][p] - a[q][q]) < 1e-30:
            angle = math.pi / 4.0
        else:
            angle = 0.5 * math.atan2(2.0 * a[p][q], a[q][q] - a[p][p])
        c = math.cos(angle)
        s = math.sin(angle)

        app = c * c * a[p][p] - 2.0 * s * c * a[p][q] + s * s * a[q][q]
        aqq = s * s * a[p][p] + 2.0 * s * c * a[p][q] + c * c * a[q][q]
        a[p][p] = app
        a[q][q] = aqq
        a[p][q] = 0.0
        a[q][p] = 0.0

        for r in range(n):
            if r in (p, q):
                continue
            arp = c * a[r][p] - s * a[r][q]
            arq = s * a[r][p] + c * a[r][q]
            a[r][p] = a[p][r] = arp
            a[r][q] = a[q][r] = arq

        for r in range(n):
            vrp = c * v[r][p] - s * v[r][q]
            vrq = s * v[r][p] + c * v[r][q]
            v[r][p] = vrp
            v[r][q] = vrq

    eigenvalues = [a[i][i] for i in range(n)]
    eigenvectors = [[v[row][col] for row in range(n)] for col in range(n)]
    order = sorted(range(n), key=lambda i: eigenvalues[i], reverse=True)
    return [eigenvalues[i] for i in order], [eigenvectors[i] for i in order]


def _classical_mds(distance: list[list[float]], dimensions: int) -> tuple[list[list[float]], dict[str, Any]]:
    n = len(distance)
    if n == 0:
        return [], {"positive_eigenvalues": 0, "negative_eigenvalues": 0}
    if n == 1:
        return [[0.0] * dimensions], {"positive_eigenvalues": 0, "negative_eigenvalues": 0}

    d2 = [[distance[i][j] ** 2 for j in range(n)] for i in range(n)]
    row_means = [sum(row) / n for row in d2]
    col_means = [sum(d2[i][j] for i in range(n)) / n for j in range(n)]
    total_mean = sum(row_means) / n
    b = [[-0.5 * (d2[i][j] - row_means[i] - col_means[j] + total_mean) for j in range(n)] for i in range(n)]

    eigenvalues, eigenvectors = _jacobi_eigen_symmetric(b)
    positive = [(value, vec) for value, vec in zip(eigenvalues, eigenvectors) if value > 1e-9]
    coords = [[0.0] * dimensions for _ in range(n)]
    for dim, (value, vec) in enumerate(positive[:dimensions]):
        scale = math.sqrt(value)
        for i in range(n):
            coords[i][dim] = vec[i] * scale
    neg_mass = sum(abs(v) for v in eigenvalues if v < -1e-9)
    pos_mass = sum(v for v in eigenvalues if v > 1e-9)
    diagnostics = {
        "positive_eigenvalues": sum(1 for v in eigenvalues if v > 1e-9),
        "negative_eigenvalues": sum(1 for v in eigenvalues if v < -1e-9),
        "positive_eigenvalue_mass": _round(pos_mass),
        "negative_eigenvalue_abs_mass": _round(neg_mass),
        "negative_eigenvalue_fraction": _round(neg_mass / (pos_mass + neg_mass) if (pos_mass + neg_mass) else 0.0),
        "dimensions_requested": dimensions,
        "dimensions_filled": min(dimensions, len(positive)),
        "note": "Classical MDS on non-Euclidean distances can have negative eigenvalues; coordinates are a best-effort 2D map.",
    }
    return coords, diagnostics





def _center_coordinates(coords: list[list[float]]) -> list[list[float]]:
    if not coords:
        return coords
    dims = len(coords[0]) if coords[0] else 0
    if dims == 0:
        return coords
    means = [sum(row[dim] for row in coords) / len(coords) for dim in range(dims)]
    return [[row[dim] - means[dim] for dim in range(dims)] for row in coords]


def _coordinate_energy(coords: list[list[float]]) -> float:
    return sum(value * value for row in coords for value in row)


def _ensure_nmds_initial_spread(coords: list[list[float]], *, seed: int) -> list[list[float]]:
    if not coords:
        return coords
    rng = random.Random(seed)
    dims = len(coords[0]) if coords[0] else 2
    out = [row[:] for row in coords]
    if _coordinate_energy(out) <= 1e-18:
        out = [[rng.uniform(-0.5, 0.5) for _ in range(dims)] for _ in out]
    # A purely one-dimensional classical solution can trap an iterative NMDS
    # update in a line.  Add a tiny deterministic perturbation to any empty axis.
    for dim in range(dims):
        if sum(row[dim] * row[dim] for row in out) <= 1e-18:
            for idx, row in enumerate(out):
                row[dim] = 1e-3 * math.sin((idx + 1) * (dim + 1) * 1.61803398875)
    return _center_coordinates(out)


def _nmds_pair_groups(distance: list[list[float]]) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float, int]]]:
    pairs: list[tuple[int, int, float]] = []
    for i in range(len(distance)):
        for j in range(i + 1, len(distance)):
            delta = max(0.0, float(distance[i][j]))
            if delta > 1e-12:
                pairs.append((i, j, delta))
    pairs.sort(key=lambda item: item[2])
    groups: list[tuple[int, int, float, int]] = []
    start = 0
    while start < len(pairs):
        end = start + 1
        total = pairs[start][2]
        while end < len(pairs) and abs(pairs[end][2] - pairs[start][2]) <= 1e-12:
            total += pairs[end][2]
            end += 1
        groups.append((start, end, total / (end - start), end - start))
        start = end
    return pairs, groups


def _pava_non_decreasing(values: list[float], weights: list[float]) -> list[float]:
    if not values:
        return []
    blocks: list[dict[str, float | int]] = []
    for idx, (value, weight) in enumerate(zip(values, weights)):
        w = max(1e-12, float(weight))
        blocks.append({"start": idx, "end": idx + 1, "weight": w, "mean": float(value)})
        while len(blocks) >= 2 and float(blocks[-2]["mean"]) > float(blocks[-1]["mean"]):
            right = blocks.pop()
            left = blocks.pop()
            weight_sum = float(left["weight"]) + float(right["weight"])
            mean = (
                float(left["mean"]) * float(left["weight"]) +
                float(right["mean"]) * float(right["weight"])
            ) / weight_sum
            blocks.append({
                "start": int(left["start"]),
                "end": int(right["end"]),
                "weight": weight_sum,
                "mean": mean,
            })
    fitted = [0.0] * len(values)
    for block in blocks:
        for idx in range(int(block["start"]), int(block["end"])):
            fitted[idx] = float(block["mean"])
    return fitted


def _pairwise_euclidean(coords: list[list[float]], pairs: list[tuple[int, int, float]]) -> list[float]:
    values: list[float] = []
    for i, j, _delta in pairs:
        total = 0.0
        for dim in range(len(coords[i])):
            diff = coords[i][dim] - coords[j][dim]
            total += diff * diff
        values.append(math.sqrt(max(0.0, total)))
    return values


def _monotone_disparities(
    pair_distances: list[float],
    groups: list[tuple[int, int, float, int]],
) -> list[float]:
    if not pair_distances:
        return []
    group_means: list[float] = []
    weights: list[float] = []
    for start, end, _delta, weight in groups:
        group_means.append(sum(pair_distances[start:end]) / max(1, end - start))
        weights.append(float(weight))
    fitted_groups = _pava_non_decreasing(group_means, weights)
    disparities = [0.0] * len(pair_distances)
    for group_index, (start, end, _delta, _weight) in enumerate(groups):
        value = fitted_groups[group_index]
        for idx in range(start, end):
            disparities[idx] = value

    # NMDS disparities are only defined up to scale.  Keep their norm aligned
    # with the current configuration so stress values are comparable across
    # iterations and restarts.
    dist_norm = math.sqrt(sum(value * value for value in pair_distances))
    disp_norm = math.sqrt(sum(value * value for value in disparities))
    if dist_norm > 1e-12 and disp_norm > 1e-12:
        scale = dist_norm / disp_norm
        disparities = [value * scale for value in disparities]
    return disparities


def _nmds_stress(pair_distances: list[float], disparities: list[float]) -> float:
    if not pair_distances or not disparities:
        return 0.0
    sse = sum((distance - disparity) ** 2 for distance, disparity in zip(pair_distances, disparities))
    denom = sum(distance * distance for distance in pair_distances)
    if denom <= 1e-18:
        denom = sum(disparity * disparity for disparity in disparities)
    return math.sqrt(sse / denom) if denom > 1e-18 else 0.0


def _nmds_once(
    distance: list[list[float]],
    *,
    dimensions: int,
    iterations: int,
    seed: int,
    init: str,
) -> tuple[list[list[float]], dict[str, Any]]:
    n = len(distance)
    if n == 0:
        return [], {"stress": 0.0, "iterations": 0, "init": init}
    if n == 1:
        return [[0.0] * dimensions], {"stress": 0.0, "iterations": 0, "init": init}

    pairs, groups = _nmds_pair_groups(distance)
    if not pairs:
        return [[0.0] * dimensions for _ in range(n)], {"stress": 0.0, "iterations": 0, "init": init, "pair_count": 0}

    rng = random.Random(seed)
    if init == "classical_mds":
        coords, _diagnostics = _classical_mds(distance, dimensions)
        coords = _ensure_nmds_initial_spread(coords, seed=seed)
    else:
        coords = [[rng.uniform(-0.5, 0.5) for _ in range(dimensions)] for _ in range(n)]
        coords = _center_coordinates(coords)

    best_coords = [row[:] for row in coords]
    best_stress = float("inf")
    previous_stress: float | None = None
    converged = False
    iteration_count = 0

    for iteration in range(max(1, iterations)):
        pair_distances = _pairwise_euclidean(coords, pairs)
        disparities = _monotone_disparities(pair_distances, groups)
        stress = _nmds_stress(pair_distances, disparities)
        iteration_count = iteration + 1
        if stress < best_stress:
            best_stress = stress
            best_coords = [row[:] for row in coords]
        if previous_stress is not None and abs(previous_stress - stress) <= 1e-7 * max(1.0, previous_stress):
            converged = True
            break
        previous_stress = stress

        transformed = [[0.0] * dimensions for _ in range(n)]
        degrees = [0.0] * n
        for (i, j, _delta), current_distance, disparity in zip(pairs, pair_distances, disparities):
            if current_distance <= 1e-12:
                continue
            ratio = disparity / current_distance
            degrees[i] += 1.0
            degrees[j] += 1.0
            for dim in range(dimensions):
                diff = coords[i][dim] - coords[j][dim]
                contribution = ratio * diff
                transformed[i][dim] += contribution
                transformed[j][dim] -= contribution
        for i in range(n):
            if degrees[i] > 0:
                for dim in range(dimensions):
                    transformed[i][dim] /= degrees[i]
            else:
                transformed[i] = coords[i][:]
        coords = _center_coordinates(transformed)
        if _coordinate_energy(coords) <= 1e-18:
            coords = [[rng.uniform(-0.5, 0.5) for _ in range(dimensions)] for _ in range(n)]
            coords = _center_coordinates(coords)

    # One final stress against the retained best coordinates.
    final_pair_distances = _pairwise_euclidean(best_coords, pairs)
    final_disparities = _monotone_disparities(final_pair_distances, groups)
    final_stress = _nmds_stress(final_pair_distances, final_disparities)
    return best_coords, {
        "stress": _round(final_stress),
        "iterations": iteration_count,
        "converged": converged,
        "init": init,
        "pair_count": len(pairs),
        "monotone_groups": len(groups),
    }


def _nmds_embedding(
    distance: list[list[float]],
    dimensions: int,
    *,
    iterations: int = 80,
    restarts: int = 3,
    seed: int = 17,
) -> tuple[list[list[float]], dict[str, Any]]:
    """Non-metric MDS over the chosen profile distance matrix.

    This is intentionally distance-first, like ecological/metagenomic NMDS:
    the original sparse coverage vectors are converted to a dissimilarity
    matrix, and NMDS only tries to preserve the rank order of those
    dissimilarities.  The implementation is a small deterministic SMACOF-style
    loop with isotonic regression (PAVA) for monotone disparities.
    """

    n = len(distance)
    dimensions = max(1, int(dimensions))
    iterations = max(1, int(iterations))
    restarts = max(1, int(restarts))
    if n == 0:
        return [], {"method": "nmds", "stress": 0.0, "profile_count": 0}
    if n == 1:
        return [[0.0] * dimensions], {"method": "nmds", "stress": 0.0, "profile_count": 1, "dimensions_filled": 0}

    attempts: list[dict[str, Any]] = []
    best_coords: list[list[float]] | None = None
    best_stress = float("inf")

    init_plan = ["classical_mds"] + ["random"] * max(0, restarts - 1)
    for attempt_index, init in enumerate(init_plan):
        coords, diagnostics = _nmds_once(
            distance,
            dimensions=dimensions,
            iterations=iterations,
            seed=seed + attempt_index * 1009,
            init=init,
        )
        stress = float(diagnostics.get("stress") or 0.0)
        attempts.append(diagnostics)
        if stress < best_stress:
            best_stress = stress
            best_coords = coords

    coords = best_coords if best_coords is not None else [[0.0] * dimensions for _ in range(n)]
    diagnostics = {
        "method": "nmds",
        "basis": "rank_order_of_profile_distance_matrix",
        "distance_matrix_used": True,
        "dimensions_requested": dimensions,
        "dimensions_filled": dimensions if n > 1 else 0,
        "iterations_requested": iterations,
        "restarts_requested": restarts,
        "seed": int(seed),
        "stress": _round(best_stress),
        "attempts": attempts,
        "note": "NMDS preserves ranked dissimilarities, not exact distances; lower stress indicates a better 2D rank-order fit.",
    }
    return coords, diagnostics


def _sparse_dot(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(key, 0.0) for key, value in a.items())


def _pca_embedding(vectors: list[dict[str, float]], dimensions: int) -> tuple[list[list[float]], dict[str, Any]]:
    """Project profiles onto the top orthogonal variance directions.

    Classical MDS starts from a distance matrix.  That is useful when the
    distance is the primary object, but it can also flatten log profiles into a
    strip when the first distance component is just activity volume.  This path
    instead builds the centered profile-feature Gram matrix and returns the top
    principal-component score coordinates.  The axes are true orthogonal
    eigenvector directions of the sparse coverage matrix after the same
    normalization/filtering/weighting used for distances.
    """

    n = len(vectors)
    dimensions = max(1, int(dimensions))
    if n == 0:
        return [], {"positive_eigenvalues": 0, "method": "pca"}
    if n == 1:
        return [[0.0] * dimensions], {"positive_eigenvalues": 0, "method": "pca", "dimensions_filled": 0}

    feature_sums: defaultdict[str, float] = defaultdict(float)
    for vector in vectors:
        for key, value in vector.items():
            feature_sums[key] += value
    mean = {key: value / n for key, value in feature_sums.items() if value}
    mean_dot_mean = _sparse_dot(mean, mean)
    vector_dot_mean = [_sparse_dot(vector, mean) for vector in vectors]

    gram = [[0.0] * n for _ in range(n)]
    for i in range(n):
        gram[i][i] = _sparse_dot(vectors[i], vectors[i]) - 2.0 * vector_dot_mean[i] + mean_dot_mean
        for j in range(i + 1, n):
            value = _sparse_dot(vectors[i], vectors[j]) - vector_dot_mean[i] - vector_dot_mean[j] + mean_dot_mean
            gram[i][j] = value
            gram[j][i] = value

    eigenvalues, eigenvectors = _jacobi_eigen_symmetric(gram)
    positive = [(value, vec) for value, vec in zip(eigenvalues, eigenvectors) if value > 1e-9]
    coords = [[0.0] * dimensions for _ in range(n)]
    for dim, (value, vec) in enumerate(positive[:dimensions]):
        scale = math.sqrt(value)
        # Deterministic sign: make the largest magnitude loading positive so
        # repeated renders do not randomly mirror the map.
        max_idx = max(range(len(vec)), key=lambda idx: abs(vec[idx])) if vec else 0
        sign = -1.0 if vec and vec[max_idx] < 0 else 1.0
        for i in range(n):
            coords[i][dim] = vec[i] * scale * sign

    positive_mass = sum(value for value in eigenvalues if value > 1e-9)
    negative_mass = sum(abs(value) for value in eigenvalues if value < -1e-9)
    kept = [value for value, _ in positive[:dimensions]]
    diagnostics = {
        "method": "pca",
        "basis": "centered_sparse_profile_vectors",
        "positive_eigenvalues": sum(1 for value in eigenvalues if value > 1e-9),
        "negative_eigenvalues": sum(1 for value in eigenvalues if value < -1e-9),
        "positive_eigenvalue_mass": _round(positive_mass),
        "negative_eigenvalue_abs_mass": _round(negative_mass),
        "negative_eigenvalue_fraction": _round(negative_mass / (positive_mass + negative_mass) if (positive_mass + negative_mass) else 0.0),
        "dimensions_requested": dimensions,
        "dimensions_filled": min(dimensions, len(positive)),
        "eigenvalues_kept": [_round(value) for value in kept],
        "explained_variance_ratio_kept": [
            _round(value / positive_mass if positive_mass else 0.0)
            for value in kept
        ],
        "note": "PCA coordinates are the top orthogonal eigenvector dimensions of the centered sparse coverage matrix.",
    }
    return coords, diagnostics


def _embed_profiles(
    vectors: list[dict[str, float]],
    distance: list[list[float]],
    options: ProfileMapOptions,
) -> tuple[list[list[float]], dict[str, Any], str]:
    method = (options.embedding or "pca").strip().lower()
    if method in {"nmds", "nonmetric_mds", "non_metric_mds"}:
        coords, diagnostics = _nmds_embedding(
            distance,
            max(1, int(options.dimensions)),
            iterations=options.nmds_iterations,
            restarts=options.nmds_restarts,
            seed=options.nmds_seed,
        )
        return coords, diagnostics, "nmds"
    if method in {"mds", "classical_mds", "pcoa"}:
        coords, diagnostics = _classical_mds(distance, max(1, int(options.dimensions)))
        diagnostics = {**diagnostics, "method": "classical_mds", "distance_matrix_used": True}
        return coords, diagnostics, "classical_mds"
    coords, diagnostics = _pca_embedding(vectors, max(1, int(options.dimensions)))
    return coords, diagnostics, "pca"

def _dominant_points(profile: dict[str, Any], coverage_points: dict[str, dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for key in ("positive_counts", "skip_counts", "failure_counts"):
        counts.update(profile.get(key) or {})
    out = []
    for cp, count in counts.most_common(limit):
        meta = coverage_points.get(cp, {})
        out.append({"id": cp, "count": count, "label": meta.get("label", cp), "type": meta.get("type", "unknown")})
    return out


def _source_hash(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_log_profile_map(root: Path | str, input_path: Path | str | None = None, options: ProfileMapOptions | None = None) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = Path(input_path).resolve() if input_path else default_log_path(root_path)
    options = options or ProfileMapOptions()
    dictionary = CoverageDictionary(options.max_coverage_points)

    records = iter_main_log_records(path)
    events = _iter_profile_events(records, alpha=options.alpha, dictionary=dictionary)
    profiles = _window_profiles(events, options)
    vectors, vector_diagnostics = _prepare_vectors(profiles, options)
    distance = _distance_matrix(vectors, metric=options.distance)
    distance_diagnostics = _distance_diagnostics(distance)
    coords, embedding_diagnostics, embedding_method = _embed_profiles(vectors, distance, options)

    points: list[dict[str, Any]] = []
    for profile, coord in zip(profiles, coords):
        x = coord[0] if len(coord) > 0 else 0.0
        y = coord[1] if len(coord) > 1 else 0.0
        point = {
            "profile_id": profile["profile_id"],
            "x": _round(x),
            "y": _round(y),
            "seq_start": profile["seq_start"],
            "seq_end": profile["seq_end"],
            "event_count": profile["event_count"],
            "surprise_bits_total": profile["surprise_bits_total"],
            "nonzero_points": profile["nonzero_points"],
            "dominant_points": _dominant_points(profile, dictionary.points),
        }
        points.append(point)

    result: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "schema": PROFILE_MAP_SCHEMA,
        "generated_at": _now_iso(),
        "source": {
            "path": str(path),
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
            "sha256": _source_hash(path),
        },
        "options": {
            "window": options.window,
            "target_surprise_bits": options.target_surprise_bits,
            "stride_surprise_bits": options.stride_surprise_bits,
            "event_window": options.event_window,
            "event_stride": options.event_stride,
            "seconds_window": options.seconds_window,
            "seconds_stride": options.seconds_stride,
            "max_coverage_points": options.max_coverage_points,
            "max_profiles": options.max_profiles,
            "normalize": options.normalize,
            "distance": options.distance,
            "feature_weighting": options.feature_weighting,
            "min_df": options.min_df,
            "max_df_fraction": options.max_df_fraction,
            "metric": options.distance,
            "embedding": options.embedding,
            "dimensions": options.dimensions,
            "nmds_iterations": options.nmds_iterations,
            "nmds_restarts": options.nmds_restarts,
            "nmds_seed": options.nmds_seed,
        },
        "summary": {
            "event_count": len(events),
            "profile_count": len(profiles),
            "coverage_point_count": len(dictionary.points),
            "truncated_new_coverage_points": dictionary.truncated_new_points,
            "sparse_profile": True,
            "zero_semantics": "missing coverage point means no evidence in the profile window; explicit skips are stored separately",
            "vector_features_seen": vector_diagnostics.get("features_seen", 0),
            "vector_features_retained": vector_diagnostics.get("features_retained", 0),
        },
        "coverage_points": dictionary.points,
        "profiles": profiles,
        "distance": {
            "metric": options.distance,
            "normalization": options.normalize,
            "feature_weighting": vector_diagnostics.get("feature_weighting", options.feature_weighting),
            "vector_diagnostics": vector_diagnostics,
            "diagnostics": distance_diagnostics,
            "profile_order": [profile["profile_id"] for profile in profiles],
            "matrix_included": bool(options.include_distance_matrix),
            "matrix": distance if options.include_distance_matrix else None,
        },
        "embedding": {
            "method": embedding_method,
            "dimensions": max(1, int(options.dimensions)),
            "points": points,
            "diagnostics": embedding_diagnostics,
        },
        "warning": "This is log-derived behavioral coverage, not source-code instrumentation coverage.",
    }
    return result


def render_profile_map_svg(
    profile_map: dict[str, Any],
    *,
    width: int = 1200,
    height: int = 800,
    label_limit: int = 24,
    scale: str = "robust",
    show_labels: bool = True,
) -> str:
    """Render a readable static SVG for the profile map.

    The JSON payload keeps the exact embedding coordinates.  The SVG is a view of
    those coordinates, so it applies two display-only aids that keep a single
    outlier or repeated coordinates from turning the map into an unreadable
    strip of overlapping labels:

    * robust scaling clips the visual viewport to the 2nd-98th percentile when
      that preserves real spread, while still drawing clipped points at the edge;
    * deterministic jitter separates points that land on the same display pixel.

    Tooltips preserve the underlying profile details.
    """

    points = profile_map.get("embedding", {}).get("points", [])
    if not points:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><text x="20" y="40">no profile points</text></svg>'

    width = max(400, int(width))
    height = max(300, int(height))
    label_limit = max(0, int(label_limit))
    scale = (scale or "robust").strip().lower()
    if scale not in {"robust", "full"}:
        scale = "robust"

    raw_xs = [float(p.get("x") or 0.0) for p in points if math.isfinite(float(p.get("x") or 0.0))]
    raw_ys = [float(p.get("y") or 0.0) for p in points if math.isfinite(float(p.get("y") or 0.0))]
    if not raw_xs:
        raw_xs = [0.0]
    if not raw_ys:
        raw_ys = [0.0]

    def percentile(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
        lo = math.floor(rank)
        hi = math.ceil(rank)
        if lo == hi:
            return ordered[lo]
        return ordered[lo] * (hi - rank) + ordered[hi] * (rank - lo)

    def bounds(values: list[float]) -> tuple[float, float, bool]:
        full_min, full_max = min(values), max(values)
        if full_min == full_max:
            spread = max(1.0, abs(full_min) * 0.1)
            return full_min - spread, full_max + spread, False
        if scale == "robust" and len(values) >= 12:
            lo = percentile(values, 0.02)
            hi = percentile(values, 0.98)
            if hi > lo and (hi - lo) >= (full_max - full_min) * 0.01:
                return lo, hi, True
        return full_min, full_max, False

    min_x, max_x, clipped_x = bounds(raw_xs)
    min_y, max_y, clipped_y = bounds(raw_ys)

    left = 64.0
    right = 28.0
    top = 82.0
    bottom = 70.0
    plot_w = max(1.0, width - left - right)
    plot_h = max(1.0, height - top - bottom)

    def clamp(value: float, lo: float, hi: float) -> tuple[float, bool]:
        if value < lo:
            return lo, True
        if value > hi:
            return hi, True
        return value, False

    def sx(x: float) -> tuple[float, bool]:
        x2, was_clipped = clamp(x, min_x, max_x)
        return left + ((x2 - min_x) / (max_x - min_x)) * plot_w, was_clipped

    def sy(y: float) -> tuple[float, bool]:
        y2, was_clipped = clamp(y, min_y, max_y)
        return top + (1.0 - ((y2 - min_y) / (max_y - min_y))) * plot_h, was_clipped

    display_points: list[dict[str, Any]] = []
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, point in enumerate(points):
        x_raw = float(point.get("x") or 0.0)
        y_raw = float(point.get("y") or 0.0)
        x, cx = sx(x_raw)
        y, cy = sy(y_raw)
        item = {
            "idx": idx,
            "point": point,
            "x": x,
            "y": y,
            "raw_x": x_raw,
            "raw_y": y_raw,
            "clipped": bool(cx or cy),
        }
        display_points.append(item)
        buckets[(round(x / 8), round(y / 8))].append(idx)

    jittered = 0
    for members in buckets.values():
        if len(members) <= 1:
            continue
        radius = min(14.0, 3.0 + math.sqrt(len(members)) * 2.0)
        for offset, idx in enumerate(members):
            angle = 2.0 * math.pi * (offset / len(members))
            display_points[idx]["x"] += math.cos(angle) * radius
            display_points[idx]["y"] += math.sin(angle) * radius
            display_points[idx]["jittered"] = True
            jittered += 1

    # Label only a small, deterministic set: endpoints, high-surprise profiles,
    # and geometric outliers.  All points still have <title> tooltips.
    labels: set[int] = set()
    if label_limit and show_labels:
        labels.add(0)
        labels.add(len(points) - 1)
        surprise_rank = sorted(
            range(len(points)),
            key=lambda i: float(points[i].get("surprise_bits_total") or 0.0),
            reverse=True,
        )
        cx = sum(item["x"] for item in display_points) / len(display_points)
        cy = sum(item["y"] for item in display_points) / len(display_points)
        outlier_rank = sorted(
            range(len(display_points)),
            key=lambda i: (display_points[i]["x"] - cx) ** 2 + (display_points[i]["y"] - cy) ** 2,
            reverse=True,
        )
        for idx in surprise_rank + outlier_rank:
            labels.add(idx)
            if len(labels) >= label_limit:
                break

    summary = profile_map.get("summary", {})
    diagnostics = profile_map.get("embedding", {}).get("diagnostics", {})
    clipping_note = "robust clipped" if (clipped_x or clipped_y) and scale == "robust" else "full extent"
    subtitle = (
        f'profiles={summary.get("profile_count", len(points))} '
        f'coverage={summary.get("coverage_point_count", "?")} '
        f'labels={len(labels)} scale={scale} {clipping_note}'
    )
    neg = diagnostics.get("negative_eigenvalue_fraction")
    if neg is not None:
        subtitle += f' neg-eigen={neg}'

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        '<text x="20" y="30" fill="#e5e7eb" font-family="monospace" font-size="18">Main log behavior profile map</text>',
        f'<text x="20" y="52" fill="#9ca3af" font-family="monospace" font-size="12">{_escape_xml(str(profile_map.get("embedding", {}).get("method", "pca")))} embedding; { _escape_xml(str(profile_map.get("distance", {}).get("metric", "manhattan")))} distance diagnostics; model-relative log-derived coverage</text>',
        f'<text x="20" y="70" fill="#9ca3af" font-family="monospace" font-size="11">{_escape_xml(subtitle)}</text>',
        f'<rect x="{left:.2f}" y="{top:.2f}" width="{plot_w:.2f}" height="{plot_h:.2f}" fill="none" stroke="#1f2937" stroke-width="1"/>',
    ]

    for t in (0.25, 0.5, 0.75):
        gx = left + plot_w * t
        gy = top + plot_h * t
        parts.append(f'<line x1="{gx:.2f}" y1="{top:.2f}" x2="{gx:.2f}" y2="{top + plot_h:.2f}" stroke="#111827" stroke-width="1"/>')
        parts.append(f'<line x1="{left:.2f}" y1="{gy:.2f}" x2="{left + plot_w:.2f}" y2="{gy:.2f}" stroke="#111827" stroke-width="1"/>')

    max_surprise = max(float(p.get("surprise_bits_total") or 0.0) for p in points) or 1.0
    for item in display_points:
        point = item["point"]
        x = max(left - 12, min(width - right + 12, float(item["x"])))
        y = max(top - 12, min(height - bottom + 12, float(item["y"])))
        surprise = float(point.get("surprise_bits_total") or 0.0)
        radius = max(3.0, min(13.0, 3.0 + 8.0 * math.sqrt(max(0.0, surprise) / max_surprise)))
        opacity = 0.72 if item.get("jittered") else 0.86
        stroke = "#f59e0b" if item.get("clipped") else "#93c5fd"
        dominant = [
            {"label": dp.get("label"), "count": dp.get("count"), "type": dp.get("type")}
            for dp in (point.get("dominant_points") or [])[:4]
        ]
        title = json.dumps(
            {
                "profile_id": point.get("profile_id"),
                "seq_start": point.get("seq_start"),
                "seq_end": point.get("seq_end"),
                "event_count": point.get("event_count"),
                "surprise_bits_total": point.get("surprise_bits_total"),
                "dominant": dominant,
            },
            sort_keys=True,
        )
        parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" '
            f'fill="#60a5fa" opacity="{opacity:.2f}" stroke="{stroke}" stroke-width="1">'
            f'<title>{_escape_xml(title)}</title></circle>'
        )

    if show_labels and label_limit:
        for idx in sorted(labels):
            item = display_points[idx]
            point = item["point"]
            x = max(left - 12, min(width - right + 12, float(item["x"])))
            y = max(top - 12, min(height - bottom + 12, float(item["y"])))
            label = str(point.get("profile_id") or "")
            # Alternate label offsets to reduce remaining collisions.
            above = (idx % 2) == 0
            dy = -9 if above else 15
            parts.append(
                f'<text x="{x + 8:.2f}" y="{y + dy:.2f}" fill="#d1d5db" '
                f'font-family="monospace" font-size="10">{_escape_xml(label)}</text>'
            )

    if jittered:
        parts.append(
            f'<text x="20" y="{height - 22}" fill="#9ca3af" font-family="monospace" font-size="10">'
            f'{jittered} overlapping points were separated with deterministic display jitter; JSON coordinates are unchanged.</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_json(path: str | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    if path:
        Path(path).expanduser().resolve().write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sparse log-derived coverage profiles and a 2D behavior map.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--window", choices=["information", "events", "time"], default="information")
    parser.add_argument("--target-surprise-bits", type=float, default=512.0)
    parser.add_argument("--stride-surprise-bits", type=float, default=512.0)
    parser.add_argument("--event-window", type=int, default=500)
    parser.add_argument("--event-stride", type=int, default=500)
    parser.add_argument("--seconds-window", type=float, default=60.0)
    parser.add_argument("--seconds-stride", type=float, default=60.0)
    parser.add_argument("--max-coverage-points", type=int, default=10_000)
    parser.add_argument("--max-profiles", type=int, default=300)
    parser.add_argument("--normalize", choices=["raw", "log1p", "sqrt", "l1", "log1p_l1", "binary"], default="log1p_l1")
    parser.add_argument("--distance", "--metric", dest="distance", choices=["manhattan", "braycurtis", "weighted_jaccard", "cosine"], default="manhattan")
    parser.add_argument("--feature-weighting", choices=["none", "idf", "tfidf", "tfidf_l2"], default="tfidf")
    parser.add_argument("--min-df", type=int, default=1)
    parser.add_argument("--max-df-fraction", type=float, default=0.95)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--embedding", choices=["pca", "mds", "classical_mds", "pcoa", "nmds", "nonmetric_mds"], default="pca")
    parser.add_argument("--nmds-iterations", type=int, default=80)
    parser.add_argument("--nmds-restarts", type=int, default=3)
    parser.add_argument("--nmds-seed", type=int, default=17)
    parser.add_argument("--include-distance-matrix", action="store_true")
    parser.add_argument("--svg-output", default="")
    parser.add_argument("--svg-width", type=int, default=1200)
    parser.add_argument("--svg-height", type=int, default=800)
    parser.add_argument("--svg-label-limit", type=int, default=24)
    parser.add_argument("--svg-scale", choices=["robust", "full"], default="robust")
    parser.add_argument("--svg-no-labels", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    options = ProfileMapOptions(
        window=args.window,
        target_surprise_bits=args.target_surprise_bits,
        stride_surprise_bits=args.stride_surprise_bits,
        event_window=args.event_window,
        event_stride=args.event_stride,
        seconds_window=args.seconds_window,
        seconds_stride=args.seconds_stride,
        max_coverage_points=args.max_coverage_points,
        max_profiles=args.max_profiles,
        normalize=args.normalize,
        distance=args.distance,
        feature_weighting=args.feature_weighting,
        min_df=args.min_df,
        max_df_fraction=args.max_df_fraction,
        embedding=args.embedding,
        alpha=args.alpha,
        nmds_iterations=args.nmds_iterations,
        nmds_restarts=args.nmds_restarts,
        nmds_seed=args.nmds_seed,
        include_distance_matrix=args.include_distance_matrix,
    )
    result = build_log_profile_map(
        root=Path(args.root),
        input_path=Path(args.input) if args.input else None,
        options=options,
    )
    _write_json(args.output or None, result)
    if args.svg_output:
        Path(args.svg_output).expanduser().resolve().write_text(
            render_profile_map_svg(
                result,
                width=args.svg_width,
                height=args.svg_height,
                label_limit=args.svg_label_limit,
                scale=args.svg_scale,
                show_labels=not args.svg_no_labels,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
