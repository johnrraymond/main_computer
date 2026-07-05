from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import gzip
import json
import math
from pathlib import Path
import re
import statistics
import struct
import tempfile
from typing import Any, Iterable, Iterator
import zipfile
import zlib

from main_computer.main_log_codec import filter_records_since_minutes, iter_lex_records, parse_log_time


LOG_INPUT_SUFFIXES = {".json", ".jsonl", ".ndjson", ".lex", ".log", ".out", ".err", ".stdout", ".stderr", ".console", ".events", ".metrics", ".text", ".trace", ".txt", ".zip", ".gz"}
_TEXT_LOG_SUFFIXES = LOG_INPUT_SUFFIXES - {".zip", ".gz"}
_ROTATED_LOG_MARKERS = (".jsonl.", ".ndjson.", ".lex.", ".log.", ".out.", ".err.", ".stdout.", ".stderr.", ".console.", ".events.", ".metrics.", ".trace.", ".txt.")
_TEXT_FIELD_NAMES = {
    "chunk",
    "detail",
    "details",
    "error",
    "line",
    "log",
    "message",
    "output",
    "stderr",
    "stdout",
    "status",
    "status_preview",
    "summary",
    "text",
    "trace",
}
_NUMERIC_PAIR_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.:\\/-]*)\s*(?:=|:)\s*"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?P<unit>milliseconds|millisecond|msec|ms|seconds|second|secs|sec|s|bytes|byte|kb|mb|gb|%)?\b",
    re.IGNORECASE,
)
_DURATION_WORD_RE = re.compile(
    r"\b(?P<key>duration|elapsed|latency|runtime|wall[_ -]?clock|wait)\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?P<unit>milliseconds|millisecond|msec|ms|seconds|second|secs|sec|s)\b",
    re.IGNORECASE,
)
_DURATION_PHRASE_RE = re.compile(
    r"\b(?:after|in|took)\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?P<unit>milliseconds|millisecond|msec|ms|seconds|second|secs|sec|s)\b",
    re.IGNORECASE,
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}")

_VALUE_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/=-])"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?P<unit>milliseconds|millisecond|msec|ms|seconds|second|secs|sec|s|bytes|byte|kb|mb|gb|"
    r"tokens|token|retries|retry|attempts|attempt|errors|error|warnings|warning|failures|failure|"
    r"successes|success|requests|request|responses|response|records|record|items|item|lines|line|"
    r"chars|char|characters|character|words|word|percent|pct|%)\b",
    re.IGNORECASE,
)
_WORD_VALUE_RE = re.compile(
    r"\b(?P<key>exit(?:[_ -]?code)?|return(?:[_ -]?code)?|tokens?|retries?|attempts?|errors?|warnings?|"
    r"failures?|success(?:es)?|requests?|responses?|records?|items?|lines?|chars?|characters?|words?|"
    r"bytes?|duration|elapsed|latency|runtime|wait|pid|process(?:[_ -]?id)?)\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\S+")
_DERIVED_METRIC_PREFIX = "record."
_DERIVED_METRIC_PREFERENCE = {
    "record.text_chars": 0,
    "record.text_words": 1,
    "record.message_chars": 2,
    "record.message_words": 3,
    "record.stdout_chars": 4,
    "record.stderr_chars": 5,
    "record.interarrival_ms": 6,
    "record.text_lines": 7,
    "record.field_count": 99,
}



@dataclass(frozen=True)
class DistributionTestResult:
    name: str
    ok: bool
    value: float | int | str
    detail: str


@dataclass(frozen=True)
class MetricDistribution:
    metric: str
    count: int
    min: float
    max: float
    mean: float
    median: float
    stdev: float
    p10: float
    p25: float
    p75: float
    p90: float
    p95: float
    p99: float
    iqr: float
    mad: float
    skewness: float
    excess_kurtosis: float
    outlier_count: int
    outlier_rate: float
    zero_count: int
    zero_rate: float
    tests: tuple[DistributionTestResult, ...]
    histogram: tuple[int, ...]
    bin_edges: tuple[float, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogMetricReport:
    paths: tuple[str, ...]
    records: int
    metrics: tuple[MetricDistribution, ...]
    output_png: str
    report_json: str

    @property
    def metric_count(self) -> int:
        return len(self.metrics)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "paths": list(self.paths),
            "records": self.records,
            "metric_count": self.metric_count,
            "output_png": self.output_png,
            "report_json": self.report_json,
            "metrics": [metric.to_json_dict() for metric in self.metrics],
        }


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number



def _normalize_unit(unit: str | None) -> str:
    clean = str(unit or "").strip().lower()
    if clean in {"milliseconds", "millisecond", "msec", "ms"}:
        return "ms"
    if clean in {"seconds", "second", "secs", "sec", "s"}:
        return "s"
    if clean in {"byte", "bytes"}:
        return "bytes"
    if clean in {"kb", "mb", "gb", "%"}:
        return clean
    return ""


def _metric_name_from_text_key(raw_key: str, unit: str | None = None) -> str:
    key = str(raw_key or "").strip().strip("`'\"[]{}(),;")
    key = key.rstrip(":=")
    key = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", key)
    key = key.replace("-", "_").replace("/", "_").replace(":", "_")
    key = re.sub(r"_+", "_", key).strip("._")
    if not key:
        return ""
    clean_unit = _normalize_unit(unit)
    lowered = key.lower()
    if clean_unit and clean_unit != "%" and not lowered.endswith(f"_{clean_unit}"):
        if clean_unit == "s" and lowered.endswith(("_seconds", "_secs", "_sec")):
            return key
        if clean_unit == "ms" and lowered.endswith(("_milliseconds", "_millisecond", "_msec")):
            return key
        if clean_unit == "bytes" and lowered.endswith(("_byte", "_bytes")):
            return key
        key = f"{key}_{clean_unit}"
    elif clean_unit == "%" and not lowered.endswith(("_percent", "_pct")):
        key = f"{key}_percent"
    return key


def _spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _metric_name_from_unit(unit: str | None) -> str:
    clean = str(unit or "").strip().lower()
    unit_map = {
        "millisecond": "value_ms",
        "milliseconds": "value_ms",
        "msec": "value_ms",
        "ms": "value_ms",
        "second": "value_s",
        "seconds": "value_s",
        "sec": "value_s",
        "secs": "value_s",
        "s": "value_s",
        "byte": "bytes",
        "bytes": "bytes",
        "kb": "kb",
        "mb": "mb",
        "gb": "gb",
        "token": "tokens",
        "tokens": "tokens",
        "retry": "retries",
        "retries": "retries",
        "attempt": "attempts",
        "attempts": "attempts",
        "error": "errors",
        "errors": "errors",
        "warning": "warnings",
        "warnings": "warnings",
        "failure": "failures",
        "failures": "failures",
        "success": "successes",
        "successes": "successes",
        "request": "requests",
        "requests": "requests",
        "response": "responses",
        "responses": "responses",
        "record": "records",
        "records": "records",
        "item": "items",
        "items": "items",
        "line": "lines",
        "lines": "lines",
        "char": "chars",
        "chars": "chars",
        "character": "chars",
        "characters": "chars",
        "word": "words",
        "words": "words",
        "percent": "percent",
        "pct": "percent",
        "%": "percent",
    }
    return unit_map.get(clean, "")


def _numeric_fields_from_text(text: str) -> Iterator[tuple[str, float]]:
    used_spans: list[tuple[int, int]] = []
    seen_values: set[tuple[str, int, int]] = set()

    def emit(name: str, number: float | None, span: tuple[int, int]) -> Iterator[tuple[str, float]]:
        if not name or number is None:
            return
        marker = (name, span[0], span[1])
        if marker in seen_values:
            return
        seen_values.add(marker)
        used_spans.append(span)
        yield name, number

    for match in _NUMERIC_PAIR_RE.finditer(text):
        name = _metric_name_from_text_key(match.group("key"), match.groupdict().get("unit"))
        number = _coerce_float(match.group("value"))
        yield from emit(name, number, (match.start(), match.end()))

    for match in _DURATION_WORD_RE.finditer(text):
        name = _metric_name_from_text_key(match.group("key"), match.groupdict().get("unit"))
        number = _coerce_float(match.group("value"))
        span = (match.start(), match.end())
        if any(_spans_overlap(span, used) for used in used_spans):
            continue
        yield from emit(name, number, span)

    for match in _DURATION_PHRASE_RE.finditer(text):
        unit = _normalize_unit(match.groupdict().get("unit"))
        name = f"duration_{unit}" if unit else "duration"
        number = _coerce_float(match.group("value"))
        span = (match.start(), match.end())
        if any(_spans_overlap(span, used) for used in used_spans):
            continue
        yield from emit(name, number, span)

    for match in _WORD_VALUE_RE.finditer(text):
        name = _metric_name_from_text_key(match.group("key"))
        number = _coerce_float(match.group("value"))
        span = (match.start(), match.end())
        if any(_spans_overlap(span, used) for used in used_spans):
            continue
        yield from emit(name, number, span)

    for match in _VALUE_UNIT_RE.finditer(text):
        name = _metric_name_from_unit(match.groupdict().get("unit"))
        number = _coerce_float(match.group("value"))
        span = (match.start(), match.end())
        if any(_spans_overlap(span, used) for used in used_spans):
            continue
        yield from emit(name, number, span)

def _should_scan_text_field(prefix: str) -> bool:
    if not prefix:
        return True
    leaf = prefix.rsplit(".", 1)[-1].lower()
    if leaf in _TEXT_FIELD_NAMES:
        return True
    return any(token in leaf for token in ("message", "output", "stderr", "stdout", "trace", "log"))


def _flatten_numeric_fields(value: object, *, prefix: str = "") -> Iterator[tuple[str, float]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                continue
            name = f"{prefix}.{key}" if prefix else key
            yield from _flatten_numeric_fields(item, prefix=name)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            name = f"{prefix}.{index}" if prefix else str(index)
            yield from _flatten_numeric_fields(item, prefix=name)
        return
    if isinstance(value, str) and _should_scan_text_field(prefix):
        yield from _numeric_fields_from_text(value)
    number = _coerce_float(value)
    if number is not None and prefix:
        yield prefix, number


def _iter_text_values(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_text_values(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text_values(item)
        return
    if isinstance(value, str) and value:
        yield value


def _derived_record_metrics(record: dict[str, Any]) -> Iterator[tuple[str, float]]:
    """Emit useful event-level metrics even when logs do not carry explicit numeric fields."""

    if record:
        yield f"{_DERIVED_METRIC_PREFIX}field_count", float(len(record))

    text_values = list(_iter_text_values(record))
    if text_values:
        text = "\n".join(text_values)
        yield f"{_DERIVED_METRIC_PREFIX}text_chars", float(len(text))
        yield f"{_DERIVED_METRIC_PREFIX}text_words", float(len(_WORD_RE.findall(text)))
        yield f"{_DERIVED_METRIC_PREFIX}text_lines", float(sum(max(1, item.count("\n") + 1) for item in text_values))

    for field in ("message", "stdout", "stderr", "command", "path", "service", "kind"):
        value = record.get(field)
        if isinstance(value, str) and value:
            safe_field = field.replace("_", "-").replace("-", "_")
            yield f"{_DERIVED_METRIC_PREFIX}{safe_field}_chars", float(len(value))
            yield f"{_DERIVED_METRIC_PREFIX}{safe_field}_words", float(len(_WORD_RE.findall(value)))


def _record_time(record: dict[str, Any]) -> Any:
    return parse_log_time(record.get("at")) or parse_log_time(record.get("received_at"))


def _is_derived_metric_name(name: str) -> bool:
    return name.startswith(_DERIVED_METRIC_PREFIX)


def _metric_rank_key(name: str, values_by_metric: dict[str, list[float]]) -> tuple[int, int, int, str]:
    if _is_derived_metric_name(name):
        return (1, _DERIVED_METRIC_PREFERENCE.get(name, 50), -len(values_by_metric[name]), name)
    return (0, 0, -len(values_by_metric[name]), name)

def _looks_like_log_input(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in LOG_INPUT_SUFFIXES:
        return True
    name = path.name.lower()
    if any(marker in name for marker in _ROTATED_LOG_MARKERS):
        return True
    if suffix.startswith(".") and suffix[1:].isdigit() and any(token in name for token in (".log.", ".jsonl.", ".lex.", ".trace.")):
        return True
    if not suffix and any(token in name for token in ("log", "metric", "trace", "stdout", "stderr", "output", "event")):
        return True
    return False


def _expand_inputs(paths: Iterable[Path | str]) -> list[Path]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and _looks_like_log_input(candidate):
                    expanded.append(candidate)
        elif path.is_file():
            expanded.append(path)
    return expanded


def _record_from_key_value_line(line: str) -> dict[str, Any] | None:
    record: dict[str, Any] = {}
    for name, value in _numeric_fields_from_text(line):
        record[name] = value
    return record or None


def _iter_records_from_json_payload(payload: object) -> Iterator[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("events", "records", "logs", "items"):
            nested = payload.get(key)
            if isinstance(nested, list) and any(isinstance(item, (dict, str)) for item in nested):
                for item in nested:
                    if isinstance(item, dict):
                        yield item
                    elif isinstance(item, str) and item.strip():
                        yield {"message": item.strip()}
                return
        yield payload
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
            elif isinstance(item, str) and item.strip():
                yield {"message": item.strip()}
        return
    if isinstance(payload, str) and payload.strip():
        yield {"message": payload.strip()}


def _iter_records_from_json_text(text: str) -> Iterator[dict[str, Any]]:
    clean = text.strip()
    if not clean:
        return
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(clean)
        if not match:
            return
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return
    yield from _iter_records_from_json_payload(payload)


def _record_from_json_line(line: str) -> dict[str, Any] | None:
    for record in _iter_records_from_json_text(line):
        return record
    return None


def _iter_text_records_from_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        yielded_json = False
        for record in _iter_records_from_json_text(clean):
            yielded_json = True
            yield record
        if yielded_json:
            continue
        record = _record_from_key_value_line(clean)
        if record is not None:
            yield record
        else:
            yield {"message": clean}


def _iter_json_or_text_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        raw = path.read_text(encoding="utf-8", errors="replace")
        records = list(_iter_records_from_json_text(raw))
        if records:
            yield from records
            return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        yield from _iter_text_records_from_lines(handle)


def _iter_lex_records_from_bytes(data: bytes) -> Iterator[dict[str, Any]]:
    temp = tempfile.NamedTemporaryFile("wb", suffix=".lex", delete=False)
    try:
        with temp:
            temp.write(data)
        yield from iter_lex_records(Path(temp.name))
    finally:
        try:
            Path(temp.name).unlink()
        except FileNotFoundError:
            pass


def _inner_suffix_for_compressed_name(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".gz"):
        return Path(lower[:-3]).suffix.lower()
    return Path(lower).suffix.lower()


def _iter_records_from_bytes(data: bytes, *, name: str = "") -> Iterator[dict[str, Any]]:
    suffix = Path(name).suffix.lower()
    if suffix == ".gz":
        data = gzip.decompress(data)
        suffix = _inner_suffix_for_compressed_name(name)
    if suffix == ".lex" or data.startswith(b"!mclog-lex") or data.startswith(b"!lex"):
        yield from _iter_lex_records_from_bytes(data)
        return
    text = data.decode("utf-8", errors="replace")
    if suffix == ".json":
        records = list(_iter_records_from_json_text(text))
        if records:
            yield from records
            return
    yield from _iter_text_records_from_lines(text.splitlines())


def _iter_gzip_records(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rb") as handle:
        data = handle.read()
    yield from _iter_records_from_bytes(data, name=path.name[:-3])


def _iter_zip_records(path: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            member_path = Path(info.filename)
            suffix = member_path.suffix.lower()
            if suffix == ".gz":
                inner_suffix = _inner_suffix_for_compressed_name(info.filename)
                if inner_suffix not in _TEXT_LOG_SUFFIXES and not _looks_like_log_input(member_path):
                    continue
            elif suffix not in _TEXT_LOG_SUFFIXES and not _looks_like_log_input(member_path):
                continue
            yield from _iter_records_from_bytes(archive.read(info), name=info.filename)


def iter_log_records(paths: Iterable[Path | str], *, since_minutes: float | None = None) -> list[dict[str, Any]]:
    """Read JSONL, !lex, plain key=value logs, directories, and rotated zip archives."""

    records: list[dict[str, Any]] = []
    for path in _expand_inputs(paths):
        suffix = path.suffix.lower()
        if suffix == ".zip":
            records.extend(_iter_zip_records(path))
        elif suffix == ".gz":
            records.extend(_iter_gzip_records(path))
        elif suffix == ".lex":
            records.extend(iter_lex_records(path))
        else:
            records.extend(_iter_json_or_text_records(path))
    return filter_records_since_minutes(records, since_minutes)


def percentile(sorted_values: list[float], percent: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (float(percent) / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    fraction = rank - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def build_histogram(values: Iterable[float], *, bins: int = 20) -> tuple[tuple[int, ...], tuple[float, ...]]:
    sample = list(values)
    if not sample:
        return tuple(), tuple()
    bins = max(1, int(bins))
    lower = min(sample)
    upper = max(sample)
    if lower == upper:
        return (len(sample),), (lower, upper)
    width = (upper - lower) / bins
    counts = [0 for _ in range(bins)]
    for value in sample:
        index = int((value - lower) / width)
        if index >= bins:
            index = bins - 1
        counts[index] += 1
    edges = [lower + width * index for index in range(bins + 1)]
    return tuple(counts), tuple(edges)


def _moment(values: list[float], mean: float, power: int) -> float:
    if not values:
        return 0.0
    return sum((value - mean) ** power for value in values) / len(values)


def summarize_metric(metric: str, values: Iterable[float], *, bins: int = 20, min_samples: int = 3) -> MetricDistribution | None:
    sample = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not sample:
        return None
    count = len(sample)
    mean = statistics.fmean(sample)
    median = statistics.median(sample)
    p10 = percentile(sample, 10)
    p25 = percentile(sample, 25)
    p75 = percentile(sample, 75)
    p90 = percentile(sample, 90)
    p95 = percentile(sample, 95)
    p99 = percentile(sample, 99)
    iqr = p75 - p25
    stdev = statistics.stdev(sample) if count > 1 else 0.0
    mad = statistics.median(abs(value - median) for value in sample)
    lower_fence = p25 - 1.5 * iqr
    upper_fence = p75 + 1.5 * iqr
    outliers = [value for value in sample if value < lower_fence or value > upper_fence]
    zero_count = sum(1 for value in sample if value == 0.0)
    if stdev > 0.0:
        m3 = _moment(sample, mean, 3)
        m4 = _moment(sample, mean, 4)
        skewness = m3 / (stdev ** 3)
        excess_kurtosis = m4 / (stdev ** 4) - 3.0
    else:
        skewness = 0.0
        excess_kurtosis = 0.0
    histogram, edges = build_histogram(sample, bins=bins)
    tests = (
        DistributionTestResult(
            "sample-size",
            count >= min_samples,
            count,
            f"{count} finite sample(s); minimum requested is {min_samples}",
        ),
        DistributionTestResult(
            "spread",
            sample[-1] > sample[0],
            sample[-1] - sample[0],
            "non-zero range" if sample[-1] > sample[0] else "all values are identical",
        ),
        DistributionTestResult(
            "iqr",
            iqr > 0.0,
            iqr,
            "middle 50% has variation" if iqr > 0.0 else "middle 50% collapsed to one value",
        ),
        DistributionTestResult(
            "outlier-rate",
            (len(outliers) / count) <= 0.10,
            len(outliers) / count,
            "IQR outliers are at or below 10% of samples",
        ),
        DistributionTestResult(
            "skewness",
            abs(skewness) <= 2.0,
            skewness,
            "absolute skewness is at or below 2.0",
        ),
        DistributionTestResult(
            "tail-heaviness",
            abs(excess_kurtosis) <= 7.0,
            excess_kurtosis,
            "absolute excess kurtosis is at or below 7.0",
        ),
        DistributionTestResult(
            "zero-inflation",
            (zero_count / count) <= 0.50,
            zero_count / count,
            "zeros are at or below 50% of samples",
        ),
    )
    return MetricDistribution(
        metric=metric,
        count=count,
        min=sample[0],
        max=sample[-1],
        mean=mean,
        median=median,
        stdev=stdev,
        p10=p10,
        p25=p25,
        p75=p75,
        p90=p90,
        p95=p95,
        p99=p99,
        iqr=iqr,
        mad=mad,
        skewness=skewness,
        excess_kurtosis=excess_kurtosis,
        outlier_count=len(outliers),
        outlier_rate=len(outliers) / count,
        zero_count=zero_count,
        zero_rate=zero_count / count,
        tests=tests,
        histogram=histogram,
        bin_edges=edges,
    )


def analyze_records(
    records: Iterable[dict[str, Any]],
    *,
    metrics: Iterable[str] | None = None,
    bins: int = 20,
    min_samples: int = 3,
    max_metrics: int = 12,
    include_derived: bool = True,
) -> tuple[MetricDistribution, ...]:
    wanted_names = [metric for metric in (metrics or []) if metric]
    wanted = set(wanted_names)
    values_by_metric: dict[str, list[float]] = {}
    record_list = list(records)

    for record in record_list:
        for name, value in _flatten_numeric_fields(record):
            if wanted and name not in wanted:
                continue
            values_by_metric.setdefault(name, []).append(value)
        if include_derived:
            for name, value in _derived_record_metrics(record):
                if wanted and name not in wanted:
                    continue
                values_by_metric.setdefault(name, []).append(value)

    if include_derived and (not wanted or "record.interarrival_ms" in wanted):
        stamped = [(index, _record_time(record)) for index, record in enumerate(record_list)]
        stamped = [(index, stamp) for index, stamp in stamped if stamp is not None]
        stamped.sort(key=lambda item: (item[1], item[0]))
        previous = None
        for _, stamp in stamped:
            if previous is not None:
                delta_ms = (stamp - previous).total_seconds() * 1000.0
                if delta_ms >= 0:
                    if not wanted or "record.interarrival_ms" in wanted:
                        values_by_metric.setdefault("record.interarrival_ms", []).append(delta_ms)
            previous = stamp

    if wanted:
        ranked_names = [name for name in wanted_names if name in values_by_metric]
    else:
        ranked_names = sorted(values_by_metric, key=lambda name: _metric_rank_key(name, values_by_metric))
        ranked_names = ranked_names[: max(1, int(max_metrics))]
    summaries: list[MetricDistribution] = []
    for name in ranked_names:
        summary = summarize_metric(name, values_by_metric[name], bins=bins, min_samples=min_samples)
        if summary is not None:
            summaries.append(summary)
    return tuple(summaries)


# A tiny 5x7 all-caps bitmap font. Unknown characters render as blanks.
_FONT: dict[str, tuple[str, ...]] = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11110", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
    "%": ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
    "=": ("00000", "11111", "00000", "11111", "00000", "00000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "11100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


class _Canvas:
    def __init__(self, width: int, height: int, *, background: tuple[int, int, int] = (255, 255, 255)) -> None:
        self.width = int(width)
        self.height = int(height)
        self.pixels = bytearray(background * (self.width * self.height))

    def _offset(self, x: int, y: int) -> int:
        return (y * self.width + x) * 3

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = self._offset(x, y)
            self.pixels[offset : offset + 3] = bytes(color)

    def rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        left = max(0, int(x))
        top = max(0, int(y))
        right = min(self.width, int(x + width))
        bottom = min(self.height, int(y + height))
        if right <= left or bottom <= top:
            return
        row = bytes(color) * (right - left)
        for py in range(top, bottom):
            offset = self._offset(left, py)
            self.pixels[offset : offset + len(row)] = row

    def line(self, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
        dx = abs(x2 - x1)
        dy = -abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        error = dx + dy
        x = x1
        y = y1
        while True:
            self.set_pixel(x, y, color)
            if x == x2 and y == y2:
                break
            e2 = 2 * error
            if e2 >= dy:
                error += dy
                x += sx
            if e2 <= dx:
                error += dx
                y += sy

    def text(self, x: int, y: int, text: str, color: tuple[int, int, int] = (0, 0, 0), *, scale: int = 2) -> None:
        cursor = int(x)
        for char in text.upper():
            pattern = _FONT.get(char, _FONT[" "])
            for row_index, row in enumerate(pattern):
                for col_index, cell in enumerate(row):
                    if cell == "1":
                        self.rect(cursor + col_index * scale, y + row_index * scale, scale, scale, color)
            cursor += 6 * scale

    def png_bytes(self) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        raw = bytearray()
        row_bytes = self.width * 3
        for y in range(self.height):
            raw.append(0)
            start = y * row_bytes
            raw.extend(self.pixels[start : start + row_bytes])
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def _format_number(value: float) -> str:
    if abs(value) >= 1000 or (0 < abs(value) < 0.01):
        return f"{value:.3g}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def write_distribution_png(
    metrics: Iterable[MetricDistribution],
    output_path: Path | str,
    *,
    title: str = "Log metric distributions",
    empty_message: str = "No numeric metrics found",
) -> Path:
    metric_list = list(metrics)
    output = Path(output_path)
    width = 1000
    panel_height = 180
    top_margin = 44
    bottom_margin = 24
    height = max(180, top_margin + bottom_margin + panel_height * max(1, len(metric_list)))
    canvas = _Canvas(width, height)
    black = (22, 22, 22)
    grid = (220, 220, 220)
    bar = (77, 130, 185)
    pass_color = (46, 125, 50)
    warn_color = (198, 90, 0)

    canvas.text(24, 16, title[:80], black, scale=2)

    if not metric_list:
        canvas.text(24, 74, empty_message[:60], warn_color, scale=3)
    for index, metric in enumerate(metric_list):
        y0 = top_margin + index * panel_height
        chart_x = 300
        chart_y = y0 + 44
        chart_w = 660
        chart_h = 96
        label = f"{metric.metric} n={metric.count}"
        canvas.text(24, y0 + 18, label[:42], black, scale=2)
        summary = (
            f"min={_format_number(metric.min)} p50={_format_number(metric.median)} "
            f"p95={_format_number(metric.p95)} max={_format_number(metric.max)}"
        )
        canvas.text(24, y0 + 46, summary[:42], black, scale=1)
        failed = [test.name for test in metric.tests if not test.ok]
        status = "tests pass" if not failed else "check " + ",".join(failed[:2])
        canvas.text(24, y0 + 66, status[:42], pass_color if not failed else warn_color, scale=1)
        canvas.line(chart_x, chart_y + chart_h, chart_x + chart_w, chart_y + chart_h, black)
        canvas.line(chart_x, chart_y, chart_x, chart_y + chart_h, black)
        for tick in range(1, 4):
            y = chart_y + chart_h - tick * chart_h // 4
            canvas.line(chart_x, y, chart_x + chart_w, y, grid)
        max_count = max(metric.histogram) if metric.histogram else 0
        if max_count:
            bin_width = max(1, chart_w // len(metric.histogram))
            for bin_index, count in enumerate(metric.histogram):
                h = max(1, int((count / max_count) * (chart_h - 4))) if count else 0
                x = chart_x + bin_index * bin_width + 1
                y = chart_y + chart_h - h
                canvas.rect(x, y, max(1, bin_width - 2), h, bar)
        canvas.text(chart_x, chart_y + chart_h + 8, _format_number(metric.min)[:12], black, scale=1)
        canvas.text(chart_x + chart_w - 88, chart_y + chart_h + 8, _format_number(metric.max)[:12], black, scale=1)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canvas.png_bytes())
    return output


def build_report_from_paths(
    paths: Iterable[Path | str],
    *,
    metrics: Iterable[str] | None = None,
    output_png: Path | str,
    report_json: Path | str | None = None,
    bins: int = 20,
    min_samples: int = 3,
    max_metrics: int = 12,
    since_minutes: float | None = None,
    include_derived: bool = True,
) -> LogMetricReport:
    path_list = tuple(str(Path(path)) for path in paths)
    records = iter_log_records(path_list, since_minutes=since_minutes)
    summaries = analyze_records(
        records,
        metrics=metrics,
        bins=bins,
        min_samples=min_samples,
        max_metrics=max_metrics,
        include_derived=include_derived,
    )
    empty_message = "No log records found" if not records else "No numeric metrics found"
    png_path = write_distribution_png(summaries, output_png, empty_message=empty_message)
    json_path = ""
    report = LogMetricReport(
        paths=path_list,
        records=len(records),
        metrics=summaries,
        output_png=str(png_path),
        report_json="",
    )
    if report_json is not None:
        json_output = Path(report_json)
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(report.to_json_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        json_path = str(json_output)
        report = LogMetricReport(
            paths=path_list,
            records=len(records),
            metrics=summaries,
            output_png=str(png_path),
            report_json=json_path,
        )
        json_output.write_text(json.dumps(report.to_json_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Log file, rotated .zip archive, or directory containing .jsonl/.lex/.log/.out/.err/.trace files.",
    )
    parser.add_argument(
        "--metric",
        action="append",
        default=None,
        help="Metric field to include. Repeat to select multiple metrics. Defaults to the most populated numeric fields.",
    )
    parser.add_argument("--output-png", type=Path, default=Path("log_metric_distributions.png"), help="PNG histogram output path.")
    parser.add_argument("--report-json", type=Path, default=None, help="Optional JSON summary report output path.")
    parser.add_argument("--bins", type=int, default=20, help="Histogram bin count. Defaults to 20.")
    parser.add_argument("--min-samples", type=int, default=3, help="Minimum sample count used by the sample-size test.")
    parser.add_argument("--max-metrics", type=int, default=12, help="Maximum metrics to plot when --metric is omitted.")
    parser.add_argument(
        "--no-derived",
        action="store_false",
        dest="include_derived",
        default=True,
        help="Do not add derived event metrics such as text length and inter-arrival timing.",
    )
    parser.add_argument(
        "--since-minutes",
        type=float,
        default=None,
        help="Only include timestamped records within N minutes of the newest at/received_at timestamp.",
    )


def run_from_args(args: argparse.Namespace) -> LogMetricReport:
    return build_report_from_paths(
        args.paths,
        metrics=args.metric,
        output_png=args.output_png,
        report_json=args.report_json,
        bins=args.bins,
        min_samples=args.min_samples,
        max_metrics=args.max_metrics,
        since_minutes=args.since_minutes,
        include_derived=args.include_derived,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Main Computer log metric distributions and write a PNG graph.")
    add_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    report = run_from_args(_build_parser().parse_args(argv))
    print(json.dumps(report.to_json_dict(), indent=2, sort_keys=True))
    return 0 if report.metric_count else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
