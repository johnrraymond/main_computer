import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests


DEFAULT_MODEL = "gemma4:26b"
DEFAULT_IMAGE = Path("main_computer/gemma4-test-image.png")
DEFAULT_URL = "http://localhost:11434/api/chat"
DEFAULT_OUTPUT_ROOT = Path("diagnostics_output/rag_runs")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_image_b64(image_path: Path) -> str:
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    if not image_path.is_file():
        raise FileNotFoundError(f"Image path is not a file: {image_path}")

    raw = image_path.read_bytes()
    if not raw:
        raise ValueError(f"Image file is empty: {image_path}")

    return base64.b64encode(raw).decode("utf-8")


def image_metadata(image_path: Path) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "path": str(image_path),
        "size_bytes": image_path.stat().st_size,
        "suffix": image_path.suffix.lower(),
    }

    try:
        from PIL import Image  # Optional. Script still works without Pillow.

        with Image.open(image_path) as img:
            meta["width"] = img.width
            meta["height"] = img.height
            meta["mode"] = img.mode
            meta["format"] = img.format
    except Exception as exc:
        meta["pil_metadata_error"] = str(exc)

    return meta


def build_prompt() -> str:
    return """
You are testing whether a local Ollama vision model can actually inspect the attached PNG.

Task:
Inspect the attached image directly. Extract what is visible in the PNG. Do not describe what a generic screenshot might contain. Do not invent details.

Return JSON only. No markdown. No prose before or after the JSON.

Schema:
{
  "image_seen": true,
  "image_type": "screenshot | ui | document | photo | unknown",
  "visible_text": [
    "exact text snippets visible in the image"
  ],
  "visible_objects": [
    "concrete visible objects, controls, panels, or UI regions"
  ],
  "application_or_context": "what app, editor, terminal, browser, or UI appears to be shown",
  "layout_observations": [
    "specific visual/layout observations from the PNG"
  ],
  "extracted_problem": "the most likely problem or issue shown in the image, if any",
  "likely_css_or_ui_cause": "probable UI/CSS/container cause if the image shows a UI layout problem; otherwise null",
  "evidence": [
    "short facts from the image that support the extraction"
  ],
  "confidence": 0.0
}

Rules:
- Set image_seen to true only if you can describe the attached PNG.
- If text is visible, include as much exact visible text as you can.
- If this is a code/editor screenshot, identify the file name, visible code structure, and visible terminal output if possible.
- If you cannot inspect the image, return image_seen=false and explain why in extracted_problem.
""".strip()


def extract_json_object(raw: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
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

    # Common model behavior: wraps JSON in prose or markdown.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no JSON object braces found in model response"

    candidate = text[start : end + 1]

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"extracted JSON was {type(parsed).__name__}, not object"
    except json.JSONDecodeError as exc:
        return None, f"JSON parse failed after extraction: {exc}"


def post_non_streaming(
    url: str,
    payload: Dict[str, Any],
    connect_timeout: int,
    read_timeout: int,
) -> Tuple[str, Dict[str, Any]]:
    res = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=(connect_timeout, read_timeout),
    )
    res.raise_for_status()
    data = res.json()

    content = (
        data.get("message", {}).get("content")
        if isinstance(data.get("message"), dict)
        else None
    )

    return content or "", data


def post_streaming(
    url: str,
    payload: Dict[str, Any],
    connect_timeout: int,
    read_timeout: int,
) -> Tuple[str, Dict[str, Any]]:
    payload = dict(payload)
    payload["stream"] = True

    content_chunks = []
    thinking_chunks = []
    all_events = []
    final_payload: Dict[str, Any] = {}

    with requests.post(
        url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=(connect_timeout, read_timeout),
        stream=True,
    ) as res:
        res.raise_for_status()

        print("[gemma4-image-smoke] stream started")
        for raw_line in res.iter_lines(decode_unicode=True):
            if not raw_line:
                continue

            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                print(f"[gemma4-image-smoke] non-json stream line: {raw_line!r}")
                continue

            all_events.append(event)
            final_payload = event

            message = event.get("message")
            if isinstance(message, dict):
                content_piece = message.get("content") or ""
                thinking_piece = message.get("thinking") or ""

                if content_piece:
                    content_chunks.append(content_piece)
                    print(content_piece, end="", flush=True)

                if thinking_piece:
                    thinking_chunks.append(thinking_piece)
                    print(
                        f"\n[gemma4-image-smoke] thinking_chunk_chars={len(thinking_piece)}",
                        flush=True,
                    )

                if not content_piece and not thinking_piece:
                    print(
                        "[gemma4-image-smoke] stream message keys="
                        + ",".join(sorted(message.keys())),
                        flush=True,
                    )
            else:
                print(
                    "[gemma4-image-smoke] stream event keys="
                    + ",".join(sorted(event.keys())),
                    flush=True,
                )

            if event.get("done") is True:
                print(
                    "\n[gemma4-image-smoke] done_reason="
                    + str(event.get("done_reason")),
                    flush=True,
                )
                break

    print()

    content = "".join(content_chunks)
    thinking = "".join(thinking_chunks)

    final_payload = dict(final_payload)
    final_payload["_debug"] = {
        "event_count": len(all_events),
        "content_chars": len(content),
        "thinking_chars": len(thinking),
        "first_event": all_events[0] if all_events else None,
        "last_event": all_events[-1] if all_events else None,
    }

    if not content and thinking:
        print(
            "[gemma4-image-smoke] WARNING: model emitted thinking but no final content; "
            "try think=false, a simpler prompt, or --no-stream"
        )

    return content, final_payload


def validate_extraction(parsed: Optional[Dict[str, Any]], raw_content: str) -> Dict[str, Any]:
    failures = []
    warnings = []

    if not raw_content.strip():
        failures.append("model returned empty content")

    if parsed is None:
        failures.append("model response could not be parsed as JSON object")
        return {
            "ok": False,
            "failures": failures,
            "warnings": warnings,
            "image_seen": False,
            "visual_evidence_score": 0,
        }

    if parsed.get("image_seen") is not True:
        failures.append("parsed JSON does not assert image_seen=true")

    visible_text = parsed.get("visible_text", [])
    visible_objects = parsed.get("visible_objects", [])
    layout_observations = parsed.get("layout_observations", [])
    evidence = parsed.get("evidence", [])

    if not isinstance(visible_text, list):
        failures.append("visible_text is not a list")
        visible_text = []

    if not isinstance(visible_objects, list):
        failures.append("visible_objects is not a list")
        visible_objects = []

    if not isinstance(layout_observations, list):
        warnings.append("layout_observations is not a list")
        layout_observations = []

    if not isinstance(evidence, list):
        warnings.append("evidence is not a list")
        evidence = []

    combined = " ".join(
        str(x)
        for x in (
            visible_text
            + visible_objects
            + layout_observations
            + evidence
            + [
                parsed.get("application_or_context", ""),
                parsed.get("extracted_problem", ""),
                parsed.get("likely_css_or_ui_cause", ""),
            ]
        )
    ).lower()

    # These are expected from the supplied PNG/editor screenshot.
    evidence_terms = [
        "vscode",
        "visual studio",
        "code",
        "terminal",
        "powershell",
        "rag_gemma4_image_recognition_smoke.py",
        "gemma4",
        "python",
        "requests",
        "payload",
        "localhost",
        "11434",
        "api/chat",
        "main_computer",
        "screenshot",
    ]

    hits = sorted(term for term in evidence_terms if term in combined)
    visual_evidence_score = len(hits)

    if visual_evidence_score < 2:
        failures.append(
            "response did not contain enough evidence that the model inspected this specific PNG"
        )

    if len(visible_text) == 0 and len(visible_objects) == 0:
        failures.append("response contained no visible_text and no visible_objects")

    return {
        "ok": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "image_seen": parsed.get("image_seen") is True,
        "visual_evidence_score": visual_evidence_score,
        "visual_evidence_hits": hits,
    }


def write_report(output_root: Path, report: Dict[str, Any]) -> Path:
    run_id = report["run_id"]
    out_dir = output_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "gemma4_image_recognition_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    raw_path = out_dir / "model_response.txt"
    raw_path.write_text(report.get("raw_content", ""), encoding="utf-8")

    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test Ollama Gemma 4 image recognition against a local PNG."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", default=str(DEFAULT_IMAGE))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--read-timeout", type=int, default=600)
    parser.add_argument("--num-predict", type=int, default=700)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming. Streaming is preferred because it proves the model is alive.",
    )
    args = parser.parse_args()

    started = time.time()
    run_id = f"gemma4_image_recognition_{utc_stamp()}"

    image_path = Path(args.image)
    output_root = Path(args.output_root)

    print(f"[gemma4-image-smoke] run_id={run_id}")
    print(f"[gemma4-image-smoke] model={args.model}")
    print(f"[gemma4-image-smoke] image={image_path}")
    print(f"[gemma4-image-smoke] url={args.url}")

    try:
        img_b64 = read_image_b64(image_path)
        meta = image_metadata(image_path)

        print(f"[gemma4-image-smoke] image_size_bytes={meta.get('size_bytes')}")
        if "width" in meta and "height" in meta:
            print(f"[gemma4-image-smoke] image_dimensions={meta['width']}x{meta['height']}")

        payload = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": build_prompt(),
                    "images": [img_b64],
                }
            ],
            "stream": not args.no_stream,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "temperature": args.temperature,
                "num_predict": args.num_predict,
            },
        }

        if args.no_stream:
            raw_content, raw_response = post_non_streaming(
                args.url,
                payload,
                args.connect_timeout,
                args.read_timeout,
            )
        else:
            raw_content, raw_response = post_streaming(
                args.url,
                payload,
                args.connect_timeout,
                args.read_timeout,
            )

        parsed, parse_error = extract_json_object(raw_content)
        validation = validate_extraction(parsed, raw_content)

        elapsed_ms = int((time.time() - started) * 1000)

        report = {
            "ok": validation["ok"],
            "run_id": run_id,
            "model": args.model,
            "url": args.url,
            "image": meta,
            "elapsed_ms": elapsed_ms,
            "parse_error": parse_error,
            "parsed": parsed,
            "validation": validation,
            "raw_content": raw_content,
            "raw_response_tail": raw_response,
        }

        report_path = write_report(output_root, report)

        print(f"[gemma4-image-smoke] content_chars={len(raw_content)}")
        print(f"[gemma4-image-smoke] elapsed_ms={elapsed_ms}")
        print(f"[gemma4-image-smoke] report={report_path}")

        if parsed is not None:
            print("[gemma4-image-smoke] parsed extraction:")
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
        else:
            print("[gemma4-image-smoke] raw model response:")
            print(raw_content)

        if validation["warnings"]:
            print("[gemma4-image-smoke] warnings:")
            for warning in validation["warnings"]:
                print(f"  - {warning}")

        if not validation["ok"]:
            print("[gemma4-image-smoke] FAIL:")
            for failure in validation["failures"]:
                print(f"  - {failure}")
            if parse_error:
                print(f"  - parse_error: {parse_error}")
            return 1

        print("[gemma4-image-smoke] PASS: model returned parseable visual extraction from the PNG")
        print(
            "[gemma4-image-smoke] visual_evidence_hits="
            + ", ".join(validation.get("visual_evidence_hits", []))
        )
        return 0

    except requests.exceptions.ReadTimeout as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        print("[gemma4-image-smoke] FAIL: Ollama request timed out")
        print(f"[gemma4-image-smoke] elapsed_ms={elapsed_ms}")
        print(f"[gemma4-image-smoke] error={exc}")
        print(
            "[gemma4-image-smoke] probable_cause="
            "model too large, cold model load, image too large, or read timeout too short"
        )
        return 2

    except requests.exceptions.ConnectionError as exc:
        print("[gemma4-image-smoke] FAIL: could not connect to Ollama")
        print(f"[gemma4-image-smoke] error={exc}")
        print("[gemma4-image-smoke] check: ollama serve")
        return 3

    except requests.exceptions.HTTPError as exc:
        print("[gemma4-image-smoke] FAIL: Ollama returned HTTP error")
        print(f"[gemma4-image-smoke] error={exc}")
        if exc.response is not None:
            print(f"[gemma4-image-smoke] response_text={exc.response.text}")
        return 4

    except Exception as exc:
        print("[gemma4-image-smoke] FAIL: unexpected error")
        print(f"[gemma4-image-smoke] error_type={type(exc).__name__}")
        print(f"[gemma4-image-smoke] error={exc}")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())