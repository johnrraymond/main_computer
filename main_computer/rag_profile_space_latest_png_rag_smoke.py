#!/usr/bin/env python3
"""
Smoke-test the "latest system picture" multimodal/RAG contract.

This smoke builds or fetches a bounded latest profile-space bundle, renders two
correlated PNG measurements, attaches the PNGs to an Ollama vision model, and
asks the model to diagnose the current system state from:

  - Plot A PNG: cumulative profile space with recent/anomaly overlays
  - Plot B PNG: a second projection of the same profile set
  - recent/surprise log evidence
  - profile-space JSON summaries
  - source/code hierarchy snippets that generated the plots

Default model: gemma3:4b

The important contract is that the cumulative/full profile set anchors the
space.  The moving window and anomaly signals are overlays inside that same
space; they are not separate projections.

Examples:

  python main_computer/rag_profile_space_latest_png_rag_smoke.py --dry-run

  python main_computer/rag_profile_space_latest_png_rag_smoke.py \
      --time 12h \
      --max-latest-bytes 8388608 \
      --model gemma3:4b

  python main_computer/rag_profile_space_latest_png_rag_smoke.py \
      --emit-beacon-log \
      --beacon latest_png_decode_probe

Exit codes:
  0 pass
  1 model/validation failure
  2 no usable latest/log/profile source
  3 could not reach Ollama
  4 could not reach main-log service when required
  5 unexpected error
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zlib


# Make direct execution from a checkout robust.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


DEFAULT_MAIN_LOG_URL = os.environ.get("MAIN_COMPUTER_MAIN_LOG_URL", "http://127.0.0.1:8767")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
DEFAULT_MODEL = os.environ.get("OLLAMA_IMAGE_MODEL") or os.environ.get("OLLAMA_MODEL") or "gemma3:4b"
DEFAULT_OUTPUT_ROOT = Path("diagnostics_output") / "rag_runs"
DEFAULT_MAX_LATEST_BYTES = int(os.environ.get("MAIN_COMPUTER_LATEST_PICTURE_MAX_BYTES", str(8 * 1024 * 1024)))
DEFAULT_TIME_WINDOW = os.environ.get("MAIN_COMPUTER_LATEST_PICTURE_TIME", "-12h")
LATEST_ENDPOINT = "/v1/log/profile-space/latest"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff"


class HttpJsonError(RuntimeError):
    """HTTP JSON helper error that preserves the response body.

    urllib's HTTPError string hides the body, but Ollama puts the actionable
    reason for 400 responses in JSON, usually {"error": "..."}.
    """

    def __init__(self, url: str, status: int, reason: str, body: str):
        self.url = url
        self.status = status
        self.reason = reason
        self.body = body
        super().__init__(self._message())

    def body_excerpt(self, limit: int = 4000) -> str:
        body = (self.body or "").strip()
        if len(body) <= limit:
            return body
        return body[:limit].rstrip() + f" ... [truncated {len(body) - limit} chars]"

    def _message(self) -> str:
        excerpt = self.body_excerpt(1200)
        suffix = f": {excerpt}" if excerpt else ""
        return f"HTTP {self.status} {self.reason} from {self.url}{suffix}"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def json_dumps(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent, default=str)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha12(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()[:12]


def log(msg: str) -> None:
    print(f"[profile-space-latest-png-rag-smoke] {msg}", flush=True)


def rel(path: Path, root: Path | None = None) -> str:
    root = (root or Path.cwd()).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_duration_seconds(value: str | None, *, fallback: float = 12 * 3600.0) -> float:
    """Parse durations like -12h, 90m, 3600, 2d.

    The leading sign is ignored; this is always interpreted as a look-back
    moving window length.
    """

    text = str(value or "").strip().lower()
    if not text:
        return fallback
    if text.startswith("+") or text.startswith("-"):
        text = text[1:]
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhdw]?)", text)
    if not match:
        return fallback
    amount = float(match.group(1))
    unit = match.group(2) or "s"
    multiplier = {
        "s": 1.0,
        "m": 60.0,
        "h": 3600.0,
        "d": 86400.0,
        "w": 7.0 * 86400.0,
    }[unit]
    seconds = amount * multiplier
    if not math.isfinite(seconds) or seconds <= 0:
        return fallback
    return seconds


def normalize_lookback_time(value: str | None, *, fallback: str = DEFAULT_TIME_WINDOW) -> str:
    """Return the canonical latest-picture lookback form.

    Users may pass ``--time 12h`` because bare negative values such as
    ``--time -12h`` look like option flags to argparse.  The smoke still
    forwards the canonical negative lookback (``-12h``) to /latest so the
    endpoint contract stays explicit.
    """

    raw = str(value or fallback or "").strip().lower()
    if not raw:
        raw = str(fallback or DEFAULT_TIME_WINDOW).strip().lower()
    if raw.startswith("+") or raw.startswith("-"):
        raw = raw[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?[smhdw]?", raw):
        fallback_raw = str(fallback or DEFAULT_TIME_WINDOW).strip().lower()
        if fallback_raw.startswith("+") or fallback_raw.startswith("-"):
            fallback_raw = fallback_raw[1:]
        raw = fallback_raw if re.fullmatch(r"\d+(?:\.\d+)?[smhdw]?", fallback_raw) else "12h"
    return "-" + raw


def normalize_time_argv(argv: list[str]) -> list[str]:
    """Allow legacy ``--time -12h`` while documenting ``--time 12h``."""

    normalized: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--time" and index + 1 < len(argv):
            candidate = argv[index + 1]
            if re.fullmatch(r"-\d+(?:\.\d+)?[smhdw]?", candidate.lower()):
                normalized.append(f"--time={candidate}")
                index += 2
                continue
        normalized.append(token)
        index += 1
    return normalized


def truncate_text(text: str, max_chars: int, *, marker: str = "truncated") -> str:
    text = str(text)
    if len(text) <= max_chars:
        return text
    if max_chars <= 80:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - 80
    return text[:head].rstrip() + f"\n... [{marker}; original_chars={len(text)}] ...\n" + text[-tail:].lstrip()


@dataclass
class ByteBudget:
    max_bytes: int
    used_bytes: int = 0

    def reserve(self, label: str, size: int) -> None:
        size = int(size)
        if self.used_bytes + size > self.max_bytes:
            raise ValueError(
                f"latest bundle byte budget exceeded while adding {label}: "
                f"{self.used_bytes}+{size}>{self.max_bytes}"
            )
        self.used_bytes += size

    @property
    def remaining(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)


def _read_http_error_body(exc: HTTPError, max_bytes: int = 64 * 1024) -> str:
    try:
        return exc.read(max_bytes + 1).decode("utf-8", errors="replace")
    except Exception as body_exc:
        return f"<could not read HTTP error body: {type(body_exc).__name__}: {body_exc}>"


def http_json(url: str, *, timeout: float, max_bytes: int) -> dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as res:
            body = res.read(max_bytes + 1)
    except HTTPError as exc:
        raise HttpJsonError(url, int(exc.code), str(exc.reason), _read_http_error_body(exc)) from exc
    if len(body) > max_bytes:
        raise ValueError(f"JSON response exceeded {max_bytes} bytes: {url}")
    parsed = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON response was {type(parsed).__name__}, not object: {url}")
    return parsed


def http_bytes(url: str, *, timeout: float, max_bytes: int) -> bytes:
    req = Request(url, headers={"Accept": "*/*"})
    try:
        with urlopen(req, timeout=timeout) as res:
            body = res.read(max_bytes + 1)
    except HTTPError as exc:
        raise HttpJsonError(url, int(exc.code), str(exc.reason), _read_http_error_body(exc)) from exc
    if len(body) > max_bytes:
        raise ValueError(f"response exceeded {max_bytes} bytes: {url}")
    return body


def post_json(url: str, payload: dict[str, Any], *, timeout: float, max_bytes: int = 16 * 1024 * 1024) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as res:
            body = res.read(max_bytes + 1)
    except HTTPError as exc:
        raise HttpJsonError(url, int(exc.code), str(exc.reason), _read_http_error_body(exc)) from exc
    if len(body) > max_bytes:
        raise ValueError(f"POST response exceeded {max_bytes} bytes: {url}")
    parsed = json.loads(body.decode("utf-8", errors="replace"))
    return parsed if isinstance(parsed, dict) else {"ok": False, "raw": parsed}

def base_url_join(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def build_latest_url(args: argparse.Namespace) -> str:
    query = {
        "time": args.time,
        "max_bytes": str(args.max_latest_bytes),
    }
    if args.beacon and args.beacon != "none":
        query["beacon"] = args.beacon
    return base_url_join(args.main_log_url, args.latest_path) + "?" + urlencode(query)


def emit_beacon_log(args: argparse.Namespace, beacon: str) -> dict[str, Any]:
    payload = {
        "event": "profile_space_latest_png_rag_smoke_beacon",
        "kind": "smoke_beacon",
        "service": "main-computer-smoke",
        "route": args.latest_path,
        "message": (
            "SMOKE_BEACON latest profile-space multimodal RAG decode probe; "
            "the AI should recover this beacon from logs and plot sidecars."
        ),
        "smoke_beacon_id": beacon,
        "moving_window": args.time,
        "expected_png_contract": "two correlated profile-space PNGs with cumulative/recent/anomaly overlays",
        "expected_state": "smoke_beacon_present",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return post_json(
        base_url_join(args.main_log_url, "/v1/log/events"),
        payload,
        timeout=args.service_timeout,
        max_bytes=1024 * 1024,
    )


def _collect_manifest_artifacts(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_manifest_artifacts(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_manifest_artifacts(item, found)
    elif isinstance(value, str):
        low = value.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".json", ".log", ".txt", ".svg")) or value.startswith(("http://", "https://")):
            found.append(value)


def load_artifact_bytes(spec: str, *, root: Path, base_url: str, timeout: float, max_bytes: int) -> bytes:
    text = str(spec)
    if text.startswith(("http://", "https://")):
        return http_bytes(text, timeout=timeout, max_bytes=max_bytes)
    if text.startswith("/v1/"):
        return http_bytes(base_url_join(base_url, text), timeout=timeout, max_bytes=max_bytes)
    path = Path(text)
    if not path.is_absolute():
        path = root / path
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"artifact exceeded {max_bytes} bytes: {path}")
    return raw


def try_fetch_latest_manifest(args: argparse.Namespace, out_dir: Path) -> dict[str, Any] | None:
    if args.latest_mode == "never":
        return None
    latest_url = build_latest_url(args)
    try:
        manifest = http_json(latest_url, timeout=args.service_timeout, max_bytes=min(args.max_latest_bytes, 16 * 1024 * 1024))
    except HttpJsonError as exc:
        if args.latest_mode == "always":
            raise RuntimeError(f"latest endpoint returned {exc}") from exc
        log(f"latest endpoint unavailable; falling back to local profile build (HTTP {exc.status})")
        return None
    except HTTPError as exc:
        if args.latest_mode == "always":
            raise RuntimeError(f"latest endpoint returned HTTP {exc.code}: {latest_url}") from exc
        log(f"latest endpoint unavailable; falling back to local profile build (HTTP {exc.code})")
        return None
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        if args.latest_mode == "always":
            raise RuntimeError(f"latest endpoint unavailable: {exc}") from exc
        log(f"latest endpoint unavailable; falling back to local profile build ({type(exc).__name__}: {exc})")
        return None

    (out_dir / "latest_endpoint_manifest.json").write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    return manifest


def _profile_option_kwargs(args: argparse.Namespace, *, embedding: str) -> dict[str, Any]:
    return {
        "window": args.profile_window,
        "target_surprise_bits": args.target_surprise_bits,
        "stride_surprise_bits": args.stride_surprise_bits,
        "event_window": args.event_window,
        "event_stride": args.event_stride,
        "seconds_window": args.seconds_window,
        "seconds_stride": args.seconds_stride,
        "max_coverage_points": args.max_coverage_points,
        "max_profiles": args.max_profiles,
        "normalize": args.normalize,
        "distance": args.distance,
        "feature_weighting": args.feature_weighting,
        "min_df": args.min_df,
        "max_df_fraction": args.max_df_fraction,
        "embedding": embedding,
        "nmds_iterations": args.nmds_iterations,
        "nmds_restarts": args.nmds_restarts,
        "nmds_seed": args.nmds_seed,
        "include_distance_matrix": False,
    }



def try_load_latest_manifest_artifacts(
    latest_manifest: dict[str, Any] | None,
    args: argparse.Namespace,
    out_dir: Path,
    budget: ByteBudget,
) -> tuple[list[bytes], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Load two PNG artifacts from a future /latest manifest when present.

    The current repo snapshot does not yet expose /v1/log/profile-space/latest,
    but the smoke already understands the intended contract.  A compliant
    /latest manifest can point to local paths, absolute paths, service-relative
    paths, or URLs.  The loader keeps the same bounded byte budget.
    """

    if not latest_manifest:
        return None

    specs: list[str] = []
    _collect_manifest_artifacts(latest_manifest, specs)
    png_specs = [spec for spec in specs if str(spec).lower().split("?", 1)[0].endswith(".png")]
    json_specs = [spec for spec in specs if str(spec).lower().split("?", 1)[0].endswith(".json")]

    if len(png_specs) < 2:
        log("latest manifest did not expose two PNG artifacts; falling back to local profile build")
        return None

    root = Path(args.root).resolve()
    images: list[bytes] = []
    image_artifacts: list[dict[str, Any]] = []
    for idx, spec in enumerate(png_specs[:2]):
        raw = load_artifact_bytes(
            spec,
            root=root,
            base_url=args.main_log_url,
            timeout=args.service_timeout,
            max_bytes=max(1, budget.remaining),
        )
        if not raw.startswith(PNG_MAGIC):
            log(f"latest artifact is not a PNG, ignoring /latest path: {spec}")
            return None
        budget.reserve(f"latest_png_{idx}", len(raw))
        out_path = out_dir / f"latest_endpoint_plot_{idx}.png"
        out_path.write_bytes(raw)
        images.append(raw)
        image_artifacts.append({
            "source": spec,
            "path": rel(out_path),
            "size_bytes": len(raw),
            "sha256_12": sha12(raw),
        })

    loaded_jsons: list[dict[str, Any]] = []
    for idx, spec in enumerate(json_specs[: args.max_latest_json_artifacts]):
        if budget.remaining <= 0:
            break
        try:
            raw = load_artifact_bytes(
                spec,
                root=root,
                base_url=args.main_log_url,
                timeout=args.service_timeout,
                max_bytes=max(1, min(budget.remaining, args.max_latest_json_artifact_bytes)),
            )
            budget.reserve(f"latest_json_{idx}", len(raw))
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            loaded_jsons.append({"source": spec, "error": f"{type(exc).__name__}: {exc}"})
            continue
        out_path = out_dir / f"latest_endpoint_sidecar_{idx}.json"
        out_path.write_bytes(raw)
        loaded_jsons.append({
            "source": spec,
            "path": rel(out_path),
            "size_bytes": len(raw),
            "sha256_12": sha12(raw),
            "summary": summarize_latest_json_for_prompt(parsed),
        })

    summary_common = {
        "source": "latest_endpoint_manifest",
        "latest_manifest_summary": summarize_latest_json_for_prompt(latest_manifest),
        "loaded_json_artifacts": loaded_jsons,
        "overlay": latest_manifest.get("overlay") or latest_manifest.get("overlays") or latest_manifest.get("latest_contract") or {},
    }
    manifest = {
        "ok": True,
        "schema": "main-computer-profile-space-latest-png-rag-smoke-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "latest-endpoint",
        "latest_contract": {
            "endpoint": args.latest_path,
            "main_log_url": args.main_log_url,
            "time": args.time,
            "max_latest_bytes": args.max_latest_bytes,
            "used_latest_bytes_before_context": budget.used_bytes,
            "anchor": "cumulative",
            "artifact_retention": "the smoke consumes the newest bounded picture; time query changes overlays, not stored snapshots",
        },
        "artifacts": {
            "plot_a_png": image_artifacts[0],
            "plot_b_png": image_artifacts[1],
            "latest_manifest": rel(out_dir / "latest_endpoint_manifest.json"),
            "prompt": rel(out_dir / "model_prompt.txt"),
            "response": rel(out_dir / "model_response.txt"),
            "report": rel(out_dir / "report.json"),
        },
        "source": {
            "root": str(root),
            "service_available": True,
            "latest_endpoint_used": True,
        },
    }
    return images, manifest, summary_common, summary_common


def summarize_latest_json_for_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        preferred = [
            "ok",
            "schema",
            "state",
            "generated_at",
            "source",
            "summary",
            "overlay",
            "overlays",
            "latest_contract",
            "moving_window",
            "artifacts",
            "options",
            "embedding",
            "profiles",
        ]
        for key in preferred:
            if key not in value:
                continue
            item = value[key]
            if key == "profiles" and isinstance(item, list):
                out[key] = item[:12]
            elif key == "embedding" and isinstance(item, dict):
                points = item.get("points")
                out[key] = {k: v for k, v in item.items() if k != "points"}
                if isinstance(points, list):
                    out[key]["points_sample"] = points[:12]
            else:
                out[key] = item
        if not out:
            for key in list(value.keys())[:20]:
                out[key] = value[key]
        return out
    if isinstance(value, list):
        return value[:20]
    return value


def build_local_profile_maps(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from main_computer.log_profile_mds import ProfileMapOptions, build_log_profile_map
    except Exception as exc:
        raise RuntimeError(f"could not import local profile-map builder: {exc}") from exc

    root = Path(args.root).resolve()
    log_path = Path(args.log_path).resolve() if args.log_path else root / "runtime" / "main_log" / "main.log.lex"
    if not log_path.exists():
        raise FileNotFoundError(f"main log not found: {log_path}")

    log(f"building local PCA profile map from {rel(log_path, root)}")
    pca = build_log_profile_map(
        root=root,
        input_path=log_path,
        options=ProfileMapOptions(**_profile_option_kwargs(args, embedding="pca")),
    )
    log(f"building local NMDS profile map from {rel(log_path, root)}")
    nmds = build_log_profile_map(
        root=root,
        input_path=log_path,
        options=ProfileMapOptions(**_profile_option_kwargs(args, embedding="nmds")),
    )
    return pca, nmds


def profile_by_id(profile_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(profile.get("profile_id")): profile
        for profile in profile_map.get("profiles", []) or []
        if isinstance(profile, dict) and profile.get("profile_id") is not None
    }


def parse_float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def overlay_flags(profile_map: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    profiles = list(profile_map.get("profiles", []) or [])
    points = list(profile_map.get("embedding", {}).get("points", []) or [])
    profiles_by_id = profile_by_id(profile_map)

    surprise_values = [
        float(profile.get("surprise_bits_total") or 0.0)
        for profile in profiles
        if math.isfinite(float(profile.get("surprise_bits_total") or 0.0))
    ]
    if surprise_values:
        ordered_surprise = sorted(surprise_values)
        idx = int(max(0, min(len(ordered_surprise) - 1, math.floor((len(ordered_surprise) - 1) * args.anomaly_quantile))))
        threshold = ordered_surprise[idx]
    else:
        threshold = float("inf")

    times = [
        parse_float_or_none(profile.get("time_end"))
        for profile in profiles
    ]
    finite_times = [ts for ts in times if ts is not None]
    recent_seconds = parse_duration_seconds(args.time, fallback=parse_duration_seconds(DEFAULT_TIME_WINDOW))
    recent_by_time = bool(finite_times)
    recent_cutoff_time = max(finite_times) - recent_seconds if finite_times else None

    sorted_profiles = sorted(
        profiles,
        key=lambda profile: (
            parse_float_or_none(profile.get("time_end")) if parse_float_or_none(profile.get("time_end")) is not None else -1.0,
            int(profile.get("seq_end") or 0),
            str(profile.get("profile_id") or ""),
        ),
    )
    fallback_recent_count = args.recent_profiles
    if fallback_recent_count <= 0:
        fallback_recent_count = max(3, math.ceil(len(sorted_profiles) * args.recent_fraction))
    fallback_recent = {str(profile.get("profile_id")) for profile in sorted_profiles[-fallback_recent_count:]}

    flags: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        pid = str(profile.get("profile_id") or "")
        if not pid:
            continue
        surprise = float(profile.get("surprise_bits_total") or 0.0)
        time_end = parse_float_or_none(profile.get("time_end"))
        if recent_by_time and time_end is not None and recent_cutoff_time is not None:
            recent = time_end >= recent_cutoff_time
            recent_basis = "time"
        else:
            recent = pid in fallback_recent
            recent_basis = "profile_tail"
        anomalous = surprise >= threshold if math.isfinite(threshold) else False
        if recent and anomalous:
            overlay_class = "recent_anomalous"
        elif recent:
            overlay_class = "recent"
        elif anomalous:
            overlay_class = "anomalous"
        else:
            overlay_class = "cumulative"
        flags[pid] = {
            "profile_id": pid,
            "recent": bool(recent),
            "anomalous": bool(anomalous),
            "recent_anomalous": bool(recent and anomalous),
            "overlay_class": overlay_class,
            "recent_basis": recent_basis,
            "surprise_bits_total": surprise,
            "time_end": time_end,
            "seq_end": profile.get("seq_end"),
            "dominant_points": profile.get("dominant_points") or profiles_by_id.get(pid, {}).get("dominant_points") or [],
            "dominant_signatures": profile.get("dominant_signatures") or [],
        }

    counts: dict[str, int] = {"cumulative": 0, "recent": 0, "anomalous": 0, "recent_anomalous": 0}
    for item in flags.values():
        counts[item["overlay_class"]] = counts.get(item["overlay_class"], 0) + 1

    summary = {
        "anchor": "cumulative",
        "time": args.time,
        "recent_seconds": recent_seconds,
        "recent_basis": "time" if recent_by_time else "profile_tail",
        "recent_cutoff_time": recent_cutoff_time,
        "anomaly_quantile": args.anomaly_quantile,
        "anomaly_threshold_surprise_bits": threshold if math.isfinite(threshold) else None,
        "profile_count": len(profiles),
        "point_count": len(points),
        "counts": counts,
        "semantics": {
            "cumulative": "background historical profile cloud",
            "recent": "inside the moving window, drawn red",
            "anomalous": "high-surprise profile, drawn amber/orange",
            "recent_anomalous": "both moving-window and anomalous, drawn bright red with a ring",
        },
    }
    return flags, summary


def attach_overlay(profile_map: dict[str, Any], flags: dict[str, dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    # Deep-copy via JSON keeps the persisted sidecar plain and deterministic.
    copied = json.loads(json.dumps(profile_map, default=str))
    copied["overlay"] = summary
    copied.setdefault("latest_contract", {})
    copied["latest_contract"].update({
        "anchor": "cumulative",
        "overlays": ["recent", "anomalous", "recent_anomalous"],
        "projection_coordinates": "computed from the cumulative profile set; overlays do not define a new projection",
    })

    for point in copied.get("embedding", {}).get("points", []) or []:
        pid = str(point.get("profile_id") or "")
        point["overlay"] = flags.get(pid, {"overlay_class": "unknown", "recent": False, "anomalous": False, "recent_anomalous": False})
    for profile in copied.get("profiles", []) or []:
        pid = str(profile.get("profile_id") or "")
        profile["overlay"] = flags.get(pid, {"overlay_class": "unknown", "recent": False, "anomalous": False, "recent_anomalous": False})
    return copied


def robust_bounds(values: list[float]) -> tuple[float, float]:
    if not values:
        return -1.0, 1.0
    values = sorted(values)
    if len(values) >= 12:
        lo = values[int((len(values) - 1) * 0.02)]
        hi = values[int((len(values) - 1) * 0.98)]
        if hi > lo:
            return lo, hi
    lo, hi = min(values), max(values)
    if lo == hi:
        spread = max(1.0, abs(lo) * 0.1)
        return lo - spread, hi + spread
    return lo, hi


def point_xy(point: dict[str, Any]) -> tuple[float, float] | None:
    x = parse_float_or_none(point.get("x"))
    y = parse_float_or_none(point.get("y"))
    if x is None or y is None:
        return None
    return x, y


def scale_points(points: list[dict[str, Any]], width: int, height: int) -> list[tuple[dict[str, Any], int, int]]:
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        xy = point_xy(point)
        if xy is None:
            continue
        xs.append(xy[0])
        ys.append(xy[1])
    min_x, max_x = robust_bounds(xs)
    min_y, max_y = robust_bounds(ys)

    left, right, top, bottom = 78, 34, 92, 74
    plot_w = max(1, width - left - right)
    plot_h = max(1, height - top - bottom)

    out: list[tuple[dict[str, Any], int, int]] = []
    for point in points:
        xy = point_xy(point)
        if xy is None:
            continue
        raw_x, raw_y = xy
        x = min(max(raw_x, min_x), max_x)
        y = min(max(raw_y, min_y), max_y)
        px = int(round(left + ((x - min_x) / (max_x - min_x)) * plot_w))
        py = int(round(top + (1.0 - ((y - min_y) / (max_y - min_y))) * plot_h))
        out.append((point, px, py))
    return out


def overlay_style(overlay_class: str) -> tuple[tuple[int, int, int], int, bool]:
    if overlay_class == "recent_anomalous":
        return (255, 0, 0), 9, True
    if overlay_class == "recent":
        return (210, 20, 20), 7, False
    if overlay_class == "anomalous":
        return (230, 145, 20), 7, True
    return (42, 42, 42), 4, False


def draw_png_with_pillow(path: Path, profile_map: dict[str, Any], title: str, args: argparse.Namespace, beacon: str) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception:
        return False

    width, height = max(500, args.width), max(360, args.height)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    points = list(profile_map.get("embedding", {}).get("points", []) or [])
    scaled = scale_points(points, width, height)

    # Frame and legend.
    draw.rectangle([78, 92, width - 34, height - 74], outline=(190, 190, 190), width=1)
    draw.text((24, 20), title, fill=(0, 0, 0), font=font)
    draw.text((24, 40), "cumulative = black/gray    recent = red    anomalous = amber ring    recent+anomalous = bright red ring", fill=(40, 40, 40), font=font)
    if beacon and beacon != "none":
        draw.text((24, 60), f"SMOKE_BEACON_ID={beacon}", fill=(120, 0, 0), font=font)

    # Background points first.
    for point, px, py in scaled:
        cls = (point.get("overlay") or {}).get("overlay_class", "cumulative")
        if cls != "cumulative":
            continue
        color, radius, ring = overlay_style(cls)
        draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color, outline=color)

    # Highlight overlays on top.
    for point, px, py in scaled:
        overlay = point.get("overlay") or {}
        cls = overlay.get("overlay_class", "cumulative")
        if cls == "cumulative":
            continue
        color, radius, ring = overlay_style(cls)
        if ring:
            draw.ellipse([px - radius - 3, py - radius - 3, px + radius + 3, py + radius + 3], outline=color, width=3)
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=(255, 245, 245), outline=color, width=2)
        else:
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color, outline=(80, 0, 0))

    # Label a bounded set of highlighted points.
    highlighted = [
        (point, px, py)
        for point, px, py in scaled
        if (point.get("overlay") or {}).get("overlay_class") in {"recent", "anomalous", "recent_anomalous"}
    ]
    highlighted.sort(key=lambda item: float(item[0].get("surprise_bits_total") or 0.0), reverse=True)
    for point, px, py in highlighted[: args.label_limit]:
        label = str(point.get("profile_id") or "?")
        draw.text((px + 8, py - 8), label, fill=(0, 0, 0), font=font)

    summary = profile_map.get("overlay") or {}
    counts = summary.get("counts") or {}
    footer = (
        f"profiles={summary.get('profile_count')} recent={counts.get('recent', 0)} "
        f"anomalous={counts.get('anomalous', 0)} both={counts.get('recent_anomalous', 0)} "
        f"window={summary.get('time')}"
    )
    draw.text((24, height - 42), footer, fill=(0, 0, 0), font=font)
    draw.text((24, height - 24), "Anchor: cumulative profile space; overlays classify points inside this same coordinate system.", fill=(40, 40, 40), font=font)

    image.save(path)
    return True


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + chunk_type
        + data
        + struct.pack("!I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def write_rgb_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw_rows = []
    stride = width * 3
    for y in range(height):
        raw_rows.append(b"\x00" + bytes(pixels[y * stride : (y + 1) * stride]))
    data = PNG_MAGIC
    data += _png_chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += _png_chunk(b"IDAT", zlib.compress(b"".join(raw_rows), level=6))
    data += _png_chunk(b"IEND", b"")
    path.write_bytes(data)


def draw_png_stdlib(path: Path, profile_map: dict[str, Any], title: str, args: argparse.Namespace, beacon: str) -> None:
    # Textless fallback: still encodes the same visual measurements with a
    # deterministic color legend in the top-left corner.
    width, height = max(500, args.width), max(360, args.height)
    pixels = bytearray([255] * (width * height * 3))

    def set_px(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    def rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for y in range(max(0, y0), min(height, y1 + 1)):
            for x in range(max(0, x0), min(width, x1 + 1)):
                set_px(x, y, color)

    def circle(cx: int, cy: int, radius: int, color: tuple[int, int, int], *, fill: bool = True) -> None:
        r2 = radius * radius
        inner = max(0, radius - 2)
        inner2 = inner * inner
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy)
                if fill:
                    if d2 <= r2:
                        set_px(x, y, color)
                elif inner2 <= d2 <= r2:
                    set_px(x, y, color)

    # Frame.
    for x in range(78, width - 34):
        set_px(x, 92, (190, 190, 190))
        set_px(x, height - 74, (190, 190, 190))
    for y in range(92, height - 74):
        set_px(78, y, (190, 190, 190))
        set_px(width - 34, y, (190, 190, 190))

    # Legend blocks: cumulative, recent, anomalous, both.
    rect(24, 20, 54, 42, (42, 42, 42))
    rect(62, 20, 92, 42, (210, 20, 20))
    rect(100, 20, 130, 42, (230, 145, 20))
    rect(138, 20, 168, 42, (255, 0, 0))
    if beacon and beacon != "none":
        # A red beacon strip gives the model a visual smoke-only marker even if
        # no text renderer is available.
        rect(24, 54, min(width - 24, 24 + 12 * len(beacon)), 64, (180, 0, 0))

    points = list(profile_map.get("embedding", {}).get("points", []) or [])
    scaled = scale_points(points, width, height)
    for point, px, py in scaled:
        cls = (point.get("overlay") or {}).get("overlay_class", "cumulative")
        if cls != "cumulative":
            continue
        color, radius, _ = overlay_style(cls)
        circle(px, py, radius, color)

    for point, px, py in scaled:
        cls = (point.get("overlay") or {}).get("overlay_class", "cumulative")
        if cls == "cumulative":
            continue
        color, radius, ring = overlay_style(cls)
        if ring:
            circle(px, py, radius + 3, color, fill=False)
            circle(px, py, radius, (255, 245, 245))
            circle(px, py, max(2, radius // 2), color)
        else:
            circle(px, py, radius, color)

    write_rgb_png(path, width, height, pixels)


def render_profile_png(path: Path, profile_map: dict[str, Any], title: str, args: argparse.Namespace, beacon: str) -> dict[str, Any]:
    used_pillow = draw_png_with_pillow(path, profile_map, title, args, beacon)
    if not used_pillow:
        draw_png_stdlib(path, profile_map, title, args, beacon)
    raw = path.read_bytes()
    return {
        "path": rel(path),
        "size_bytes": len(raw),
        "sha256_12": sha12(raw),
        "renderer": "pillow" if used_pillow else "stdlib_png",
        "title": title,
    }



def coverage_points_sample(value: Any, max_items: int) -> Any:
    if isinstance(value, list):
        return value[:max_items]
    if isinstance(value, dict):
        return {
            key: value[key]
            for key in list(value.keys())[:max_items]
        }
    return []

def summarize_profile_map(profile_map: dict[str, Any], *, max_points: int, max_coverage: int) -> dict[str, Any]:
    points = list(profile_map.get("embedding", {}).get("points", []) or [])
    profiles = profile_by_id(profile_map)
    interesting = sorted(
        points,
        key=lambda point: (
            (point.get("overlay") or {}).get("overlay_class") == "recent_anomalous",
            (point.get("overlay") or {}).get("overlay_class") == "anomalous",
            (point.get("overlay") or {}).get("overlay_class") == "recent",
            float(point.get("surprise_bits_total") or 0.0),
        ),
        reverse=True,
    )[:max_points]

    summarized_points: list[dict[str, Any]] = []
    for point in interesting:
        pid = str(point.get("profile_id") or "")
        profile = profiles.get(pid, {})
        summarized_points.append({
            "profile_id": pid,
            "x": point.get("x"),
            "y": point.get("y"),
            "seq_start": point.get("seq_start"),
            "seq_end": point.get("seq_end"),
            "time_start": profile.get("time_start"),
            "time_end": profile.get("time_end"),
            "event_count": point.get("event_count"),
            "surprise_bits_total": point.get("surprise_bits_total"),
            "overlay": point.get("overlay"),
            "dominant_points": point.get("dominant_points") or profile.get("dominant_points") or [],
            "dominant_signatures": profile.get("dominant_signatures") or [],
        })

    return {
        "ok": profile_map.get("ok"),
        "schema": profile_map.get("schema"),
        "generated_at": profile_map.get("generated_at"),
        "source": profile_map.get("source"),
        "options": profile_map.get("options"),
        "summary": profile_map.get("summary"),
        "overlay": profile_map.get("overlay"),
        "embedding": {
            "method": (profile_map.get("embedding") or {}).get("method"),
            "diagnostics": (profile_map.get("embedding") or {}).get("diagnostics"),
            "selected_points": summarized_points,
        },
        "coverage_points_sample": coverage_points_sample(profile_map.get("coverage_points"), max_coverage),
        "warning": profile_map.get("warning"),
    }


def fetch_service_context(args: argparse.Namespace, out_dir: Path, budget: ByteBudget) -> dict[str, Any]:
    context: dict[str, Any] = {"service_available": False}
    if args.no_service_context:
        context["skipped"] = True
        return context

    for name, path, max_bytes in (
        ("health", "/health", 512 * 1024),
        ("recent", f"/v1/log/recent?limit={args.recent_log_limit}", 2 * 1024 * 1024),
        ("surprise", f"/v1/log/surprise?limit={args.recent_log_limit}", 2 * 1024 * 1024),
    ):
        url = base_url_join(args.main_log_url, path)
        try:
            payload = http_json(url, timeout=args.service_timeout, max_bytes=min(max_bytes, budget.remaining or max_bytes))
        except Exception as exc:
            context[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": url}
            continue
        raw = json_dumps(payload).encode("utf-8", errors="replace")
        try:
            budget.reserve(f"service_{name}", len(raw))
        except ValueError:
            payload = {"ok": False, "state": "dropped-by-byte-budget", "original_size_bytes": len(raw)}
        (out_dir / f"service_{name}.json").write_text(json_dumps(payload) + "\n", encoding="utf-8")
        context[name] = payload
        context["service_available"] = True
    return context


def read_local_log_context(args: argparse.Namespace, out_dir: Path, budget: ByteBudget) -> dict[str, Any]:
    root = Path(args.root).resolve()
    log_path = Path(args.log_path).resolve() if args.log_path else root / "runtime" / "main_log" / "main.log.lex"
    context: dict[str, Any] = {"path": str(log_path), "exists": log_path.exists()}
    if not log_path.exists():
        return context

    records: list[dict[str, Any]] = []
    try:
        from main_computer.main_log_pack import iter_main_log_records

        all_records = list(iter_main_log_records(log_path))
        records = [record for record in all_records[-args.recent_log_limit:] if isinstance(record, dict)]
        context.update({
            "record_count_seen": len(all_records),
            "records": records,
        })
    except Exception as exc:
        raw = log_path.read_bytes()
        tail = raw[-min(len(raw), args.local_log_tail_bytes) :]
        text = tail.decode("utf-8", errors="replace")
        context.update({
            "decode_error": f"{type(exc).__name__}: {exc}",
            "tail_text": text,
            "tail_bytes": len(tail),
        })

    raw_context = json_dumps(context).encode("utf-8", errors="replace")
    if len(raw_context) > min(args.max_log_text_bytes, budget.remaining):
        context["records"] = context.get("records", [])[-max(5, args.recent_log_limit // 4):]
        if "tail_text" in context:
            context["tail_text"] = truncate_text(str(context["tail_text"]), max(1000, args.max_log_text_bytes // 2))
        raw_context = json_dumps(context).encode("utf-8", errors="replace")
    if len(raw_context) <= budget.remaining:
        budget.reserve("local_log_context", len(raw_context))
    else:
        context = {"path": str(log_path), "exists": True, "state": "dropped-by-byte-budget", "size_bytes": len(raw_context)}

    (out_dir / "local_log_context.json").write_text(json_dumps(context) + "\n", encoding="utf-8")
    return context


def code_snippet_for(path: Path, markers: list[str], *, max_chars: int) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected: set[int] = set()
    low_lines = [line.lower() for line in lines]
    for marker in markers:
        marker_l = marker.lower()
        for idx, line in enumerate(low_lines):
            if marker_l in line:
                for j in range(max(0, idx - 12), min(len(lines), idx + 55)):
                    selected.add(j)
                break

    if not selected:
        snippet = truncate_text(text, max_chars, marker="code snippet truncated")
    else:
        chunks: list[str] = []
        last = -999
        for idx in sorted(selected):
            if idx != last + 1:
                if chunks:
                    chunks.append("\n# ...\n")
                chunks.append(f"# {path.name}:{idx + 1}\n")
            chunks.append(lines[idx] + "\n")
            last = idx
        snippet = truncate_text("".join(chunks), max_chars, marker="code snippet truncated")

    return {
        "path": rel(path),
        "exists": True,
        "sha256_12": sha12(path.read_bytes()),
        "snippet": snippet,
    }


def collect_code_context(args: argparse.Namespace, out_dir: Path, budget: ByteBudget) -> dict[str, Any]:
    if args.no_code_context:
        return {"skipped": True}
    root = Path(args.root).resolve()
    per_file = max(1200, args.max_code_text_bytes // 2)
    snippets = [
        code_snippet_for(
            root / "main_computer" / "main_log_service.py",
            ["/v1/log/profile-map", "_handle_profile_map", "profile_nmds_path", "profile_map_path"],
            max_chars=per_file,
        ),
        code_snippet_for(
            root / "main_computer" / "log_profile_mds.py",
            ["class ProfileMapOptions", "def build_log_profile_map", "def render_profile_map_svg", "def _slice_profile"],
            max_chars=per_file,
        ),
    ]
    context = {
        "purpose": "source/code hierarchy snippets that generate profile-space JSON/SVG and explain route/options/projection",
        "snippets": snippets,
    }
    raw = json_dumps(context).encode("utf-8", errors="replace")
    if len(raw) > budget.remaining:
        context = {"state": "dropped-by-byte-budget", "original_size_bytes": len(raw)}
    else:
        budget.reserve("code_context", len(raw))
    (out_dir / "code_context.json").write_text(json_dumps(context) + "\n", encoding="utf-8")
    return context


def choose_beacon(args: argparse.Namespace) -> str:
    if args.beacon == "none":
        return ""
    if args.beacon and args.beacon != "auto":
        safe = re.sub(r"[^A-Za-z0-9_.:-]+", "_", args.beacon).strip("_")
        return safe[:80] or "latest_png_decode_probe"
    return "latest_png_decode_probe_" + utc_stamp()


def build_prompt(
    *,
    beacon: str,
    pca_summary: dict[str, Any],
    nmds_summary: dict[str, Any],
    service_context: dict[str, Any],
    log_context: dict[str, Any],
    code_context: dict[str, Any],
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> str:
    payload = {
        "latest_picture_contract": {
            "meaning": "bounded latest diagnostic picture of the running system",
            "time": args.time,
            "max_latest_bytes": args.max_latest_bytes,
            "anchor": "cumulative profile space",
            "overlays": {
                "cumulative": "black/gray background points",
                "recent": "red moving-window points",
                "anomalous": "amber/orange high-surprise points",
                "recent_anomalous": "bright red ringed points",
            },
            "projection_pair": {
                "image_0": "PCA-style profile-space PNG",
                "image_1": "NMDS-style profile-space PNG",
                "correlation_rule": "both images encode the same underlying profile ids/features with different coordinates",
            },
        },
        "smoke_beacon_id": beacon or None,
        "manifest": manifest,
        "pca_profile_space_summary": pca_summary,
        "nmds_profile_space_summary": nmds_summary,
        "service_context": service_context,
        "log_context": log_context,
        "code_context": code_context,
    }
    sidecars = json_dumps(payload)
    sidecars = truncate_text(sidecars, args.max_prompt_sidecar_chars, marker="prompt sidecars truncated")

    task = args.task.strip() or (
        "Use the two PNG plots plus the provided log/code/profile sidecars to diagnose "
        "the current state of the system at this moment."
    )

    return f"""
You are a Main Computer multimodal RAG diagnostic smoke model.

You are given two attached PNG images plus bounded text sidecars.

The attached PNGs are not decoration. Treat them as measurements:
- image[0] is a cumulative profile-space plot using a PCA-style projection.
- image[1] is a correlated profile-space plot using an NMDS-style projection.
- Both images should show the same underlying profile set and overlays.
- Black/gray means cumulative background.
- Red means the moving-window recent overlay.
- Amber/orange means anomalous high-surprise profiles.
- Bright red ring means both recent and anomalous.

Task:
{task}

Use RAG over the provided logs, profile JSON summaries, and code snippets as needed.
Decide whether the latest picture is enough to diagnose the current system state right now.

Return JSON only, with this schema:
{{
  "image_seen": true,
  "plots_decoded": [
    {{
      "image_index": 0,
      "projection": "pca | nmds | unknown",
      "cumulative_background_seen": true,
      "recent_overlay_seen": true,
      "anomaly_overlay_seen": true,
      "recent_anomalous_overlay_seen": true,
      "visual_evidence": ["specific visual facts from this plot"]
    }}
  ],
  "two_plot_correlation": {{
    "same_underlying_profile_set": true,
    "shared_clusters_or_regions": ["what both plots agree on"],
    "differences_explained_by_projection": ["how the geometries differ while facts remain shared"]
  }},
  "log_evidence": ["specific facts from the logs or recent/surprise context"],
  "code_hierarchy_evidence": ["specific route/module/function evidence from code snippets"],
  "anomaly_diagnosis": {{
    "recent_anomalies": ["profile ids, regions, services, routes, modules, or signatures implicated"],
    "older_anomalies": ["older anomaly evidence, if any"],
    "dominant_features": ["coverage/log features that explain the highlighted points"]
  }},
  "current_system_state": "concise diagnosis of the system right now",
  "latest_picture_is_enough": true,
  "missing_evidence_if_not_enough": ["exact artifact, log, route, source file, or time window needed"],
  "smoke_beacon_decode": {{
    "beacon_seen": true,
    "beacon_id": "{beacon}"
  }},
  "confidence": 0.0
}}

Rules:
- If the images are unreadable, set image_seen=false and explain what failed.
- Do not invent clusters that are not supported by either PNG or the sidecars.
- If the two plots disagree, say so.
- If the latest picture is not enough for diagnosis, name the exact missing evidence.
- If a smoke beacon appears in logs, JSON, or image text, decode it exactly.

Bounded sidecars:
<<<LATEST_PROFILE_SPACE_RAG_SIDECARS_JSON
{sidecars}
LATEST_PROFILE_SPACE_RAG_SIDECARS_JSON
""".strip()


def extract_json_object(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = raw.strip()
    if not text:
        return None, "empty model response"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"top-level JSON was {type(parsed).__name__}, not object"
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object braces found"
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"extracted JSON was {type(parsed).__name__}, not object"
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed: {exc}"


def build_ollama_payload(args: argparse.Namespace, prompt: str, images: list[bytes], *, minimal: bool = False) -> dict[str, Any]:
    """Build an Ollama /api/chat payload.

    Keep the default payload deliberately conservative. Some Ollama builds reject
    the newer `think` field with HTTP 400, and the smoke does not need it.
    """
    encoded_images = [base64.b64encode(image).decode("ascii") for image in images]
    system_prompt = "You are a strict JSON-only multimodal RAG diagnostic smoke assistant."

    if minimal:
        return {
            "model": args.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": system_prompt + "\n\n" + prompt,
                    "images": encoded_images,
                }
            ],
        }

    options: dict[str, Any] = {
        "temperature": args.temperature,
        "num_predict": args.num_predict,
    }
    if getattr(args, "num_ctx", 0) and args.num_ctx > 0:
        options["num_ctx"] = args.num_ctx

    payload: dict[str, Any] = {
        "model": args.model,
        "stream": False,
        "options": options,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
                "images": encoded_images,
            },
        ],
    }
    if args.keep_alive:
        payload["keep_alive"] = args.keep_alive
    return payload


def payload_without_image_bodies(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    messages = redacted.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("images"), list):
                message["images"] = [f"<base64 image {i} redacted>" for i, _ in enumerate(message["images"])]
    return redacted


def call_ollama(args: argparse.Namespace, prompt: str, images: list[bytes]) -> tuple[str, dict[str, Any]]:
    payload = build_ollama_payload(args, prompt, images, minimal=False)
    prompt_bytes = len(prompt.encode("utf-8", errors="replace"))
    redacted_payload = payload_without_image_bodies(payload)
    redacted_payload_bytes = len(json.dumps(redacted_payload, ensure_ascii=False).encode("utf-8"))
    full_payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    log(
        "posting to Ollama "
        f"model={args.model} prompt_bytes={prompt_bytes} "
        f"payload_bytes_without_image_bodies={redacted_payload_bytes} "
        f"payload_bytes_with_images={full_payload_bytes} image_count={len(images)}"
    )
    try:
        data = post_json(args.ollama_url, payload, timeout=args.ollama_timeout, max_bytes=32 * 1024 * 1024)
    except HttpJsonError as exc:
        if exc.status == 400:
            log("Ollama returned HTTP 400; retrying once with minimal /api/chat payload")
            minimal_payload = build_ollama_payload(args, prompt, images, minimal=True)
            try:
                data = post_json(args.ollama_url, minimal_payload, timeout=args.ollama_timeout, max_bytes=32 * 1024 * 1024)
            except HttpJsonError as retry_exc:
                combined = (
                    "Ollama rejected both the normal and minimal chat payloads.\n"
                    f"normal_payload_error: {exc}\n"
                    f"minimal_payload_error: {retry_exc}"
                )
                raise HttpJsonError(args.ollama_url, retry_exc.status, retry_exc.reason, combined) from retry_exc
        else:
            raise
    message = data.get("message")
    if isinstance(message, dict):
        content = str(message.get("content") or "")
    else:
        content = str(data.get("response") or "")
    return content, data

def validate_response(parsed: dict[str, Any] | None, raw: str, parse_error: str | None, *, beacon: str, args: argparse.Namespace) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    low = raw.lower()

    if args.dry_run:
        warnings.append("dry-run did not call Ollama")
        return {"ok": True, "failures": failures, "warnings": warnings, "parse_error": None}

    if parsed is None:
        failures.append(parse_error or "model response was not parseable JSON")
    else:
        if parsed.get("image_seen") is not True:
            failures.append("image_seen was not true")
        plots = parsed.get("plots_decoded")
        if not isinstance(plots, list) or len(plots) < 2:
            failures.append("plots_decoded did not include both attached plots")
        if "current_system_state" not in parsed:
            failures.append("current_system_state missing")
        if "latest_picture_is_enough" not in parsed:
            failures.append("latest_picture_is_enough missing")
        corr = parsed.get("two_plot_correlation")
        if not isinstance(corr, dict):
            failures.append("two_plot_correlation missing or not an object")

    for marker in ("cannot see the image", "can't see the image", "unable to view the image", "no image provided"):
        if marker in low:
            failures.append(f"image refusal marker found: {marker!r}")
            break

    if beacon and args.require_beacon_decode and beacon.lower() not in low:
        failures.append(f"smoke beacon was not decoded: {beacon!r}")

    if not args.require_beacon_decode and beacon:
        warnings.append("beacon decode was not required; use --require-beacon-decode for strict channel-binding")
    return {"ok": not failures, "failures": failures, "warnings": warnings, "parse_error": parse_error}


def build_manifest(
    *,
    out_dir: Path,
    beacon: str,
    latest_endpoint_manifest: dict[str, Any] | None,
    pca_artifact: dict[str, Any],
    nmds_artifact: dict[str, Any],
    pca_summary: dict[str, Any],
    nmds_summary: dict[str, Any],
    service_context: dict[str, Any],
    args: argparse.Namespace,
    budget: ByteBudget,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "main-computer-profile-space-latest-png-rag-smoke-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "smoke_beacon_id": beacon or None,
        "artifact_dir": rel(out_dir),
        "mode": "latest-endpoint" if latest_endpoint_manifest is not None else "local-profile-build",
        "latest_contract": {
            "endpoint": args.latest_path,
            "main_log_url": args.main_log_url,
            "time": args.time,
            "max_latest_bytes": args.max_latest_bytes,
            "used_latest_bytes_before_prompt": budget.used_bytes,
            "remaining_latest_bytes_before_prompt": budget.remaining,
            "anchor": "cumulative",
            "moving_window": "overlay-only; does not define a new projection",
            "artifact_retention": "this smoke consumes the newest bounded picture; it does not require storing every historical time query",
        },
        "artifacts": {
            "plot_a_pca_png": pca_artifact,
            "plot_b_nmds_png": nmds_artifact,
            "profile_space_pca_json": rel(out_dir / "profile_space_pca_overlay.json"),
            "profile_space_nmds_json": rel(out_dir / "profile_space_nmds_overlay.json"),
            "prompt": rel(out_dir / "model_prompt.txt"),
            "response": rel(out_dir / "model_response.txt"),
            "report": rel(out_dir / "report.json"),
        },
        "source": {
            "root": str(Path(args.root).resolve()),
            "log_path": str((Path(args.log_path).resolve() if args.log_path else Path(args.root).resolve() / "runtime" / "main_log" / "main.log.lex")),
            "service_available": bool(service_context.get("service_available")),
            "latest_endpoint_used": latest_endpoint_manifest is not None,
        },
        "plots": {
            "pca_summary": pca_summary.get("overlay"),
            "nmds_summary": nmds_summary.get("overlay"),
        },
    }


def prepare_latest_bundle(args: argparse.Namespace, out_dir: Path, beacon: str) -> tuple[list[bytes], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    budget = ByteBudget(args.max_latest_bytes)

    latest_manifest = try_fetch_latest_manifest(args, out_dir)
    loaded_latest = try_load_latest_manifest_artifacts(latest_manifest, args, out_dir, budget)
    if loaded_latest is not None:
        images, manifest, pca_summary, nmds_summary = loaded_latest
        service_context = fetch_service_context(args, out_dir, budget)
        log_context = read_local_log_context(args, out_dir, budget)
        code_context = collect_code_context(args, out_dir, budget)
        manifest["latest_contract"]["used_latest_bytes_before_prompt"] = budget.used_bytes
        manifest["latest_contract"]["remaining_latest_bytes_before_prompt"] = budget.remaining
        (out_dir / "latest_picture_manifest.json").write_text(json_dumps(manifest) + "\n", encoding="utf-8")
        return images, manifest, pca_summary, nmds_summary, service_context, log_context, code_context

    pca_map, nmds_map = build_local_profile_maps(args)

    flags, overlay_summary = overlay_flags(pca_map, args)
    pca_overlay = attach_overlay(pca_map, flags, overlay_summary)
    nmds_overlay = attach_overlay(nmds_map, flags, {**overlay_summary, "projection_overlay_note": "same flags as PCA; coordinates differ by projection"})

    pca_json_path = out_dir / "profile_space_pca_overlay.json"
    nmds_json_path = out_dir / "profile_space_nmds_overlay.json"
    pca_json_path.write_text(json_dumps(pca_overlay) + "\n", encoding="utf-8")
    nmds_json_path.write_text(json_dumps(nmds_overlay) + "\n", encoding="utf-8")

    # The full JSON files are artifacts; the prompt only receives summaries.
    budget.reserve("profile_space_pca_json", min(pca_json_path.stat().st_size, args.count_profile_json_bytes))
    budget.reserve("profile_space_nmds_json", min(nmds_json_path.stat().st_size, args.count_profile_json_bytes))

    pca_png_path = out_dir / "profile_space_plot_a_pca.png"
    nmds_png_path = out_dir / "profile_space_plot_b_nmds.png"
    pca_artifact = render_profile_png(
        pca_png_path,
        pca_overlay,
        f"Plot A: latest cumulative profile space PCA overlay window={args.time}",
        args,
        beacon,
    )
    nmds_artifact = render_profile_png(
        nmds_png_path,
        nmds_overlay,
        f"Plot B: latest cumulative profile space NMDS overlay window={args.time}",
        args,
        beacon,
    )

    pca_bytes = pca_png_path.read_bytes()
    nmds_bytes = nmds_png_path.read_bytes()
    if not pca_bytes.startswith(PNG_MAGIC):
        raise ValueError(f"PCA plot was not a PNG: {pca_png_path}")
    if not nmds_bytes.startswith(PNG_MAGIC):
        raise ValueError(f"NMDS plot was not a PNG: {nmds_png_path}")
    budget.reserve("profile_space_pca_png", len(pca_bytes))
    budget.reserve("profile_space_nmds_png", len(nmds_bytes))

    service_context = fetch_service_context(args, out_dir, budget)
    log_context = read_local_log_context(args, out_dir, budget)
    code_context = collect_code_context(args, out_dir, budget)

    pca_summary = summarize_profile_map(pca_overlay, max_points=args.max_summary_points, max_coverage=args.max_summary_coverage_points)
    nmds_summary = summarize_profile_map(nmds_overlay, max_points=args.max_summary_points, max_coverage=args.max_summary_coverage_points)

    manifest = build_manifest(
        out_dir=out_dir,
        beacon=beacon,
        latest_endpoint_manifest=latest_manifest,
        pca_artifact=pca_artifact,
        nmds_artifact=nmds_artifact,
        pca_summary=pca_summary,
        nmds_summary=nmds_summary,
        service_context=service_context,
        args=args,
        budget=budget,
    )
    (out_dir / "latest_picture_manifest.json").write_text(json_dumps(manifest) + "\n", encoding="utf-8")

    return [pca_bytes, nmds_bytes], manifest, pca_summary, nmds_summary, service_context, log_context, code_context


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test multimodal diagnosis from latest profile-space PNGs plus logs/code RAG sidecars."
    )
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--log-path", default="", help="Explicit main.log.lex path. Defaults to runtime/main_log/main.log.lex under --root.")
    parser.add_argument("--main-log-url", default=DEFAULT_MAIN_LOG_URL)
    parser.add_argument("--latest-path", default=LATEST_ENDPOINT)
    parser.add_argument("--latest-mode", choices=["auto", "always", "never"], default="auto", help="Use /latest manifest when available. The local renderer remains the fallback.")
    parser.add_argument("--time", default=DEFAULT_TIME_WINDOW, help="Moving-window lookback, e.g. 1h, 12h, 2d. Values are interpreted as lookbacks; do not include a leading '-' in PowerShell.")
    parser.add_argument("--max-latest-bytes", type=int, default=DEFAULT_MAX_LATEST_BYTES, help="Default byte contract for the bounded latest picture.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))

    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-timeout", type=float, default=180.0)
    parser.add_argument("--service-timeout", type=float, default=6.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-predict", type=int, default=700)
    parser.add_argument("--num-ctx", type=int, default=16384, help="Ollama context window to request through options.num_ctx; use 0 to omit.")
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--pull", action="store_true", help="Run `ollama pull <model>` before calling Ollama.")
    parser.add_argument("--dry-run", action="store_true", help="Build plots, prompt, and report without calling Ollama.")

    parser.add_argument("--profile-window", choices=["information", "events", "time"], default="information")
    parser.add_argument("--target-surprise-bits", type=float, default=512.0)
    parser.add_argument("--stride-surprise-bits", type=float, default=512.0)
    parser.add_argument("--event-window", type=int, default=500)
    parser.add_argument("--event-stride", type=int, default=500)
    parser.add_argument("--seconds-window", type=float, default=60.0)
    parser.add_argument("--seconds-stride", type=float, default=60.0)
    parser.add_argument("--max-coverage-points", type=int, default=5000)
    parser.add_argument("--max-profiles", type=int, default=120)
    parser.add_argument("--normalize", choices=["raw", "log1p", "sqrt", "l1", "log1p_l1", "binary"], default="log1p_l1")
    parser.add_argument("--distance", choices=["manhattan", "braycurtis", "weighted_jaccard", "cosine"], default="manhattan")
    parser.add_argument("--feature-weighting", choices=["none", "idf", "tfidf", "tfidf_l2"], default="tfidf")
    parser.add_argument("--min-df", type=int, default=1)
    parser.add_argument("--max-df-fraction", type=float, default=0.95)
    parser.add_argument("--nmds-iterations", type=int, default=40)
    parser.add_argument("--nmds-restarts", type=int, default=1)
    parser.add_argument("--nmds-seed", type=int, default=17)

    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--label-limit", type=int, default=18)
    parser.add_argument("--recent-profiles", type=int, default=0, help="Fallback recent count if profile timestamps are unavailable.")
    parser.add_argument("--recent-fraction", type=float, default=0.12)
    parser.add_argument("--anomaly-quantile", type=float, default=0.95)

    parser.add_argument("--recent-log-limit", type=int, default=120)
    parser.add_argument("--local-log-tail-bytes", type=int, default=256 * 1024)
    parser.add_argument("--max-log-text-bytes", type=int, default=512 * 1024)
    parser.add_argument("--max-code-text-bytes", type=int, default=60_000)
    parser.add_argument("--max-prompt-sidecar-chars", type=int, default=24_000, help="Bound text sidecars inside the Ollama prompt; lower this if Ollama reports context errors.")
    parser.add_argument("--max-summary-points", type=int, default=24)
    parser.add_argument("--max-summary-coverage-points", type=int, default=80)
    parser.add_argument("--max-latest-json-artifacts", type=int, default=4, help="When /latest exists, load up to this many JSON sidecars.")
    parser.add_argument("--max-latest-json-artifact-bytes", type=int, default=512 * 1024, help="Per-JSON-artifact byte cap for /latest sidecars.")
    parser.add_argument("--count-profile-json-bytes", type=int, default=512 * 1024, help="How much each full profile JSON artifact counts against latest byte budget.")

    parser.add_argument("--no-service-context", action="store_true")
    parser.add_argument("--no-code-context", action="store_true")
    parser.add_argument("--beacon", default="auto", help="Smoke beacon id; use 'none' to disable or 'auto' to generate one.")
    parser.add_argument("--emit-beacon-log", action="store_true", help="POST a smoke-only beacon event to /v1/log/events before building the plots.")
    parser.add_argument("--require-beacon-decode", action="store_true", help="Fail if the model does not decode the beacon.")
    parser.add_argument("--task", default="", help="Override the default diagnostic task in the model prompt.")
    args = parser.parse_args(normalize_time_argv(argv))
    args.time = normalize_lookback_time(args.time)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    started = time.perf_counter()
    root = Path(args.root).resolve()
    run_id = "profile_space_latest_png_rag_smoke_" + utc_stamp()
    out_dir = ensure_dir(Path(args.output_root) / run_id)

    try:
        if args.max_latest_bytes < 512 * 1024:
            raise ValueError("--max-latest-bytes is too small for two PNGs plus sidecars; use at least 524288")

        beacon = choose_beacon(args)
        if args.emit_beacon_log:
            log(f"emitting smoke beacon to main-log service: {beacon}")
            emit_result = emit_beacon_log(args, beacon)
            (out_dir / "emitted_beacon_log_result.json").write_text(json_dumps(emit_result) + "\n", encoding="utf-8")
            if not emit_result.get("ok"):
                log(f"beacon log append returned non-ok: {emit_result}")
            time.sleep(args.service_timeout if args.service_timeout < 0.25 else 0.25)

        images, manifest, pca_summary, nmds_summary, service_context, log_context, code_context = prepare_latest_bundle(args, out_dir, beacon)

        prompt = build_prompt(
            beacon=beacon,
            pca_summary=pca_summary,
            nmds_summary=nmds_summary,
            service_context=service_context,
            log_context=log_context,
            code_context=code_context,
            manifest=manifest,
            args=args,
        )
        (out_dir / "model_prompt.txt").write_text(prompt, encoding="utf-8")

        log(f"latest artifact dir={out_dir}")
        log(f"attached PNGs: {len(images)}; bytes={[len(image) for image in images]}")
        log(f"max_latest_bytes={args.max_latest_bytes}")
        log(f"model={args.model}")

        raw_response = ""
        response_data: dict[str, Any] = {}
        parsed: dict[str, Any] | None = None
        parse_error: str | None = None

        if args.dry_run:
            raw_response = ""
            validation = validate_response(None, "", None, beacon=beacon, args=args)
        else:
            if args.pull:
                subprocess.run(["ollama", "pull", args.model], check=True)
            raw_response, response_data = call_ollama(args, prompt, images)
            (out_dir / "model_response_raw.json").write_text(json_dumps(response_data) + "\n", encoding="utf-8")
            (out_dir / "model_response.txt").write_text(raw_response, encoding="utf-8")
            parsed, parse_error = extract_json_object(raw_response)
            if parsed is not None:
                (out_dir / "model_response_parsed.json").write_text(json_dumps(parsed) + "\n", encoding="utf-8")
            validation = validate_response(parsed, raw_response, parse_error, beacon=beacon, args=args)

        report = {
            "ok": bool(validation["ok"]),
            "run_id": run_id,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "schema": "main-computer-profile-space-latest-png-rag-smoke-report-v1",
            "manifest": manifest,
            "validation": validation,
            "parsed": parsed,
            "raw_response_preview": truncate_text(raw_response, 4000, marker="response preview truncated"),
            "dry_run": bool(args.dry_run),
            "argv": sys.argv[1:] if argv is None else argv,
        }
        (out_dir / "report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")

        for warning in validation.get("warnings", []):
            log(f"WARNING: {warning}")
        if not validation["ok"]:
            log("FAIL")
            for failure in validation.get("failures", []):
                print(f"  - {failure}", file=sys.stderr)
            return 1

        log("PASS")
        log(f"report={out_dir / 'report.json'}")
        return 0

    except FileNotFoundError as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: {exc}", file=sys.stderr)
        return 2
    except HttpJsonError as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: {exc}", file=sys.stderr)
        return 4
    except HTTPError as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: HTTP {exc.code}: {exc}", file=sys.stderr)
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
            if body:
                print(f"[profile-space-latest-png-rag-smoke] HTTP body: {body}", file=sys.stderr)
        except Exception:
            pass
        return 4
    except URLError as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: could not reach service: {exc}", file=sys.stderr)
        return 4
    except TimeoutError as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: timeout: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"[profile-space-latest-png-rag-smoke] FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
