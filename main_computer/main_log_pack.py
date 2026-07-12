from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable
import zipfile

from main_computer.log_surprise_compressor import bit_entropy, signature_for_event
from main_computer.main_log_codec import canonical_json_line, iter_lex_records


SCHEMA_VERSION = 1
PACK_SCHEMA = "mclog-surprise-pack-v1"


@dataclass(frozen=True)
class MainLogPackOptions:
    alpha: float = 0.5
    top: int = 200
    histogram_bins: int = 16
    surprise_threshold_bits: float = 8.0
    include_lossless_source: bool = False
    include_surprise_literals: bool = True
    include_report: bool = True
    compression: str = "lzma"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: bytes | str, *, chars: int = 16, prefix: bool = True) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "replace")
    digest = hashlib.sha256(value).hexdigest()[:chars]
    return f"sha256:{digest}" if prefix else digest


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def _iter_jsonl_records(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"message": line, "kind": "raw-line"}
        if isinstance(payload, dict):
            yield payload
        else:
            yield {"message": line, "kind": "raw-line"}


def default_log_path(root: Path) -> Path:
    lex = root / "runtime" / "main_log" / "main.log.lex"
    if lex.exists():
        return lex
    return root / "runtime" / "main_log" / "main.log.jsonl"


def iter_main_log_records(path: Path) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".lex":
        yield from iter_lex_records(path)
    elif suffix in {".jsonl", ".log"}:
        yield from _iter_jsonl_records(path)
    else:
        # Try the lex decoder first because main.log.lex is the canonical path.
        try:
            yield from iter_lex_records(path)
        except Exception:
            yield from _iter_jsonl_records(path)


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


def _compact_record(record: dict[str, Any], *, preview_chars: int = 600) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "ingest_seq",
        "at",
        "received_at",
        "kind",
        "service",
        "source_service",
        "stream",
        "process_name",
        "returncode",
        "exit_code",
        "pid",
    ):
        value = record.get(key)
        if value not in (None, ""):
            compact[key] = value
    message = record.get("message")
    if message not in (None, ""):
        compact["message_preview"] = str(message)[:preview_chars]
    command = record.get("command")
    if command not in (None, ""):
        compact["command_preview"] = str(command)[:preview_chars]
    return compact


def _zip_compression_method(name: str) -> int:
    mode = str(name or "lzma").strip().lower()
    if mode == "stored":
        return zipfile.ZIP_STORED
    if mode == "deflate":
        return zipfile.ZIP_DEFLATED
    if mode == "bzip2":
        return zipfile.ZIP_BZIP2
    return zipfile.ZIP_LZMA


def _json_bytes(payload: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_surprise_pack(
    *,
    root: Path | str,
    input_path: Path | str | None = None,
    options: MainLogPackOptions | None = None,
) -> dict[str, Any]:
    """Build a semantic surprise-coded pack for the current main log.

    This pack is intentionally model-relative. It preserves the sequence of
    normalized signatures and surprise codes, plus selected literal surprise
    records. It is not a lossless reconstruction unless the caller also embeds
    the source log with ``include_lossless_source`` when writing the zip.
    """

    opts = options or MainLogPackOptions()
    root_path = Path(root).resolve()
    path = Path(input_path).resolve() if input_path else default_log_path(root_path).resolve()
    source_bytes = _read_bytes(path)
    source_size = len(source_bytes)

    counts: Counter[str] = Counter()
    signature_ids: dict[str, int] = {}
    signature_meta: dict[int, dict[str, Any]] = {}
    runs: list[list[int]] = []
    surprise_values: list[float] = []
    entropy_values: list[float] = []
    raw_bytes_by_signature: Counter[int] = Counter()
    top_candidates: list[dict[str, Any]] = []
    literal_records: list[dict[str, Any]] = []

    def _signature_id(sig: str, seq: int, raw_hash: str) -> int:
        existing = signature_ids.get(sig)
        if existing is not None:
            return existing
        sid = len(signature_ids)
        signature_ids[sig] = sid
        signature_meta[sid] = {
            "id": sid,
            "hash": _sha(sig),
            "template": sig[:1000],
            "count": 0,
            "first_seq": seq,
            "last_seq": seq,
            "max_surprise_centibits": 0,
            "last_surprise_centibits": 0,
            "example_raw_hash": raw_hash,
        }
        return sid

    total_raw_record_bytes = 0
    records_observed = 0
    for seq, record in enumerate(iter_main_log_records(path), start=1):
        records_observed = seq
        raw_line = canonical_json_line(record)
        raw_bytes = raw_line.encode("utf-8", "replace")
        total_raw_record_bytes += len(raw_bytes)

        sig = signature_for_event(record)
        raw_hash = _sha(raw_bytes)
        sid = _signature_id(sig, seq, raw_hash)
        prior_count = counts[sig]
        prior_total = seq - 1
        if prior_total <= 0:
            probability = 1.0
            surprise = 0.0
        else:
            vocabulary = len(signature_ids)
            denominator = prior_total + opts.alpha * (vocabulary + 1)
            probability = (prior_count + opts.alpha) / denominator
            surprise = -math.log2(probability)

        entropy = bit_entropy(raw_bytes)
        surprise_centibits = int(round(surprise * 100))
        entropy_millibits = int(round(entropy * 1000))
        raw_len = len(raw_bytes)

        counts[sig] += 1
        surprise_values.append(surprise)
        entropy_values.append(entropy)
        raw_bytes_by_signature[sid] += raw_len

        meta = signature_meta[sid]
        meta["count"] = int(counts[sig])
        meta["last_seq"] = seq
        meta["last_surprise_centibits"] = surprise_centibits
        meta["max_surprise_centibits"] = max(int(meta["max_surprise_centibits"]), surprise_centibits)

        # Run code: [signature_id, first_seq, count, first_surprise_cb,
        # last_surprise_cb, min_surprise_cb, max_surprise_cb, raw_bytes_sum,
        # last_line_entropy_millibits]
        if runs and runs[-1][0] == sid:
            run = runs[-1]
            run[2] += 1
            run[4] = surprise_centibits
            run[5] = min(run[5], surprise_centibits)
            run[6] = max(run[6], surprise_centibits)
            run[7] += raw_len
            run[8] = entropy_millibits
        else:
            runs.append([sid, seq, 1, surprise_centibits, surprise_centibits, surprise_centibits, surprise_centibits, raw_len, entropy_millibits])

        candidate = {
            "seq": seq,
            "signature_id": sid,
            "signature_hash": meta["hash"],
            "surprise_bits": _round(surprise),
            "surprise_centibits": surprise_centibits,
            "probability_estimate": round(probability, 12),
            "prior_count": prior_count,
            "prior_total": prior_total,
            "line_entropy_bits_per_bit": _round(entropy),
            "raw_bytes": raw_len,
            "raw_hash": raw_hash,
            "signature_preview": str(meta["template"])[:240],
            "record_preview": _compact_record(record),
        }
        top_candidates.append(candidate)
        top_candidates.sort(key=lambda item: (int(item["surprise_centibits"]), int(item["seq"])), reverse=True)
        del top_candidates[max(1, int(opts.top)) :]

        if opts.include_surprise_literals and surprise >= opts.surprise_threshold_bits:
            literal_records.append({"seq": seq, "signature_id": sid, "canonical_json": raw_line})

    signatures = []
    for sid, meta in sorted(signature_meta.items()):
        signatures.append(
            [
                sid,
                meta["hash"],
                int(meta["count"]),
                int(meta["first_seq"]),
                int(meta["last_seq"]),
                int(meta["max_surprise_centibits"]),
                int(meta["last_surprise_centibits"]),
                int(raw_bytes_by_signature[sid]),
                meta["example_raw_hash"],
                meta["template"],
            ]
        )

    dominant_count = max((int(meta["count"]) for meta in signature_meta.values()), default=0)
    semantic_stream_bytes = len(_json_bytes({"signatures": signatures, "runs": runs, "surprise_literals": literal_records}))
    pack = {
        "schema_version": SCHEMA_VERSION,
        "schema": PACK_SCHEMA,
        "ok": True,
        "created_at": _now_iso(),
        "root": str(root_path),
        "source": {
            "path": str(path),
            "exists": path.exists(),
            "kind": "lex" if path.suffix.lower() == ".lex" else "jsonl_or_text",
            "source_file_bytes": source_size,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest() if source_bytes else "",
            "decoded_records": records_observed,
            "canonical_record_bytes": total_raw_record_bytes,
        },
        "mode": {
            "name": "semantic_surprise_compression",
            "lossless_reconstruction": False,
            "lossless_source_embedded": bool(opts.include_lossless_source),
            "warning": "The surprise pack preserves normalized signatures, runs, histograms, hashes, and selected literal surprise records. Exact raw reconstruction requires include_lossless_source=true.",
        },
        "model": {
            "event_unit": "normalized_log_signature",
            "surprise": "-log2(P(signature | prior stream))",
            "mathematical_log_base": 2,
            "alpha": opts.alpha,
            "volatile_fields_discounted": True,
            "random_payloads_do_not_create_surprise": True,
            "run_code_layout": [
                "signature_id",
                "first_seq",
                "count",
                "first_surprise_centibits",
                "last_surprise_centibits",
                "min_surprise_centibits",
                "max_surprise_centibits",
                "raw_bytes_sum",
                "last_line_entropy_millibits",
            ],
            "signature_layout": [
                "id",
                "hash",
                "count",
                "first_seq",
                "last_seq",
                "max_surprise_centibits",
                "last_surprise_centibits",
                "raw_bytes_sum",
                "example_raw_hash",
                "template",
            ],
        },
        "summary": {
            "total_events": records_observed,
            "unique_signatures": len(signature_ids),
            "run_count": len(runs),
            "dominant_signature_fraction": round(dominant_count / records_observed, 6) if records_observed else 0.0,
            "literal_surprise_records": len(literal_records),
            "max_surprise_bits": _round(max(surprise_values) if surprise_values else 0.0),
            "mean_surprise_bits": _round(sum(surprise_values) / len(surprise_values) if surprise_values else 0.0),
            "mean_line_entropy_bits_per_bit": _round(sum(entropy_values) / len(entropy_values) if entropy_values else 0.0),
            "semantic_stream_bytes_before_zip": semantic_stream_bytes,
            "semantic_to_canonical_record_ratio_before_zip": round(semantic_stream_bytes / total_raw_record_bytes, 6) if total_raw_record_bytes else 0.0,
            "semantic_to_source_file_ratio_before_zip": round(semantic_stream_bytes / source_size, 6) if source_size else 0.0,
        },
        "histograms": {
            "surprise_bits": _histogram(surprise_values, opts.histogram_bins),
            "line_entropy_bits_per_bit": _histogram(entropy_values, opts.histogram_bins),
        },
        "signatures": signatures,
        "runs": runs,
        "top_surprise_events": top_candidates[: max(1, int(opts.top))],
        "surprise_literals": literal_records,
    }
    return pack


def build_main_log_pack_zip_bytes(
    *,
    root: Path | str,
    input_path: Path | str | None = None,
    options: MainLogPackOptions | None = None,
) -> bytes:
    opts = options or MainLogPackOptions()
    root_path = Path(root).resolve()
    path = Path(input_path).resolve() if input_path else default_log_path(root_path).resolve()
    pack = build_surprise_pack(root=root_path, input_path=path, options=opts)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "archive": "main-log-surprise-pack",
        "created_at": _now_iso(),
        "root": str(root_path),
        "source_path": str(path),
        "pack_schema": PACK_SCHEMA,
        "contents": [
            "manifest.json",
            "main-log-surprise-pack.json",
        ],
        "usage": {
            "powershell_stdout": "python -m main_computer.main_log_pack --root . --output - > main-log-surprise-pack.zip",
            "powershell_output_file": "python -m main_computer.main_log_pack --root . --output runtime/main_log/main-log-surprise-pack.zip",
            "http": "curl.exe -o main-log-surprise-pack.zip http://127.0.0.1:8767/v1/log/compress",
        },
        "warning": pack["mode"]["warning"],
    }

    compression_method = _zip_compression_method(opts.compression)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression_method, compresslevel=None) as zf:
        zf.writestr("manifest.json", _json_bytes(manifest, pretty=True))
        zf.writestr("main-log-surprise-pack.json", _json_bytes(pack, pretty=False))
        if opts.include_report:
            report = {
                "schema_version": SCHEMA_VERSION,
                "created_at": _now_iso(),
                "source": pack["source"],
                "mode": pack["mode"],
                "model": pack["model"],
                "summary": pack["summary"],
                "histograms": pack["histograms"],
                "top_surprise_events": pack["top_surprise_events"],
            }
            zf.writestr("main-log-surprise-report.json", _json_bytes(report, pretty=True))
            manifest["contents"].append("main-log-surprise-report.json")
        if opts.include_lossless_source and path.exists():
            name = "source/" + path.name
            zf.writestr(name, path.read_bytes())
            manifest["contents"].append(name)
            # Rewrite the manifest last with the final content list.
            zf.writestr("manifest.final.json", _json_bytes(manifest, pretty=True))
    return buffer.getvalue()


def write_main_log_pack_zip(
    *,
    root: Path | str,
    output: Path | str | None,
    input_path: Path | str | None = None,
    options: MainLogPackOptions | None = None,
) -> dict[str, Any]:
    data = build_main_log_pack_zip_bytes(root=root, input_path=input_path, options=options)
    if output is None or str(output) == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return {"ok": True, "output": "-", "bytes": len(data)}
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"ok": True, "output": str(target), "bytes": len(data)}


def _bool_arg(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compress the current Main Computer main log into a surprise-coded zip archive.")
    parser.add_argument("--root", default=".", help="Main Computer root. Defaults to the current directory.")
    parser.add_argument("--input", default="", help="Input log path. Defaults to runtime/main_log/main.log.lex under --root.")
    parser.add_argument("--output", default="-", help="Output zip path, or '-' for stdout. Defaults to stdout.")
    parser.add_argument("--top", type=int, default=200, help="Number of top surprise events to retain.")
    parser.add_argument("--surprise-threshold", type=float, default=8.0, help="Store literal canonical JSON for events at or above this surprise.")
    parser.add_argument("--alpha", type=float, default=0.5, help="Laplace smoothing alpha.")
    parser.add_argument("--bins", type=int, default=16, help="Histogram bin count.")
    parser.add_argument("--compression", choices=["lzma", "deflate", "bzip2", "stored"], default="lzma", help="Zip member compression method.")
    parser.add_argument("--include-lossless-source", action="store_true", help="Embed the source log for exact reconstruction/audit. Larger.")
    parser.add_argument("--no-surprise-literals", action="store_true", help="Do not store literal high-surprise records.")
    parser.add_argument("--no-report", action="store_true", help="Only include manifest and compact pack.")
    parser.add_argument("--json-status", action="store_true", help="Print status JSON to stderr after writing the zip.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    opts = MainLogPackOptions(
        alpha=float(args.alpha),
        top=max(1, int(args.top)),
        histogram_bins=max(1, int(args.bins)),
        surprise_threshold_bits=float(args.surprise_threshold),
        include_lossless_source=bool(args.include_lossless_source),
        include_surprise_literals=not bool(args.no_surprise_literals),
        include_report=not bool(args.no_report),
        compression=str(args.compression),
    )
    input_path = args.input or None
    result = write_main_log_pack_zip(root=Path(args.root), input_path=input_path, output=args.output, options=opts)
    if args.json_status or args.output != "-":
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
