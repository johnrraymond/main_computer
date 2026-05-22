#!/usr/bin/env python3
"""
Compact model-in-the-loop claim-grounding smoke for generated repo edits.

This smoke tests the general groundwork-first process without asking the model
for one giant dossier. The first model call must produce a small grounding card.

Pipeline:

    evidence fixture
      -> model produces compact claim-grounding card
      -> deterministic verifier decides whether generation is allowed
      -> optional model patch proposal
      -> deterministic verifier checks proposal against the card

The model is not trusted to be right. It must expose:
    - exact source evidence it is acting on
    - preservation invariants
    - edit-relevant claims and verification status
    - deterministic checks that a future patch must pass
    - whether generation should proceed

If parsing fails and a retry is attempted, the report preserves both raw attempts
instead of letting an empty retry erase the first model response diagnostics.

This smoke does NOT execute generated editor code.
This smoke does NOT modify the real target file.
This smoke DOES call local Ollama unless --offline-self-check is used.

Examples:

    python main_computer/rag_generated_editor_claim_grounding_smoke.py --offline-self-check

    python main_computer/rag_generated_editor_claim_grounding_smoke.py ^
      --model gemma4:26b --timeout-seconds 600 --num-predict 500 ^
      --skip-patch-proposal

If JSON mode misbehaves for a local model, try:

    python main_computer/rag_generated_editor_claim_grounding_smoke.py ^
      --model gemma4:26b --timeout-seconds 600 --num-predict 500 ^
      --skip-patch-proposal --format-mode none

Thinking-capable Ollama models can spend the whole token budget in the
separate thinking stream before producing final response text. This smoke
defaults --think-mode false and reports thinking diagnostics separately so an
empty response is not mistaken for a transport success.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_generated_editor_claim_grounding_smoke"
TARGET_PATH = "main_computer/web/applications/scripts/chat-console.js"

ALLOWED_CLAIM_KINDS = {
    "source_observation",
    "text_transformation",
    "language_semantics",
    "api_semantics",
    "project_convention",
    "runtime_behavior",
    "transformation_safety",
}

ALLOWED_VERIFICATION_STATUSES = {
    "anchored_in_evidence",
    "verified_by_trusted_rule",
    "verified_by_local_probe",
    "unverified",
    "not_needed",
}

ALLOWED_IF_UNVERIFIED = {
    "block_generation",
    "block_promotion",
    "allow_with_warning",
    "not_applicable",
}

ALLOWED_CHECK_KINDS = {
    "literal_must_contain",
    "literal_must_not_contain",
    "regex_must_match",
    "regex_must_not_match",
}

ALLOWED_CHECK_INTENTS = {
    "new_behavior",
    "preservation",
    "regression_guard",
    "syntax_shape",
    "other",
}


@dataclass
class CheckResult:
    ok: bool
    issues: list[str]
    warnings: list[str] | None = None
    blocking_reasons: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": self.issues,
            "warnings": self.warnings or [],
            "blocking_reasons": self.blocking_reasons or [],
        }


@dataclass
class OllamaGenerateResult:
    text: str
    thinking_text: str
    diagnostics: dict[str, Any]
    stream_lines: list[str]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_summary(raw: str, limit: int = 900) -> dict[str, Any]:
    if not raw:
        return {
            "raw_length": 0,
            "raw_sha256": None,
            "raw_preview": "",
        }

    preview = raw[:limit]
    if len(raw) > limit:
        preview += "...<truncated>"

    return {
        "raw_length": len(raw),
        "raw_sha256": sha256_text(raw),
        "raw_preview": preview,
    }


def detect_repo_root() -> Path:
    here = Path(__file__).resolve()
    if here.parent.name == "main_computer":
        return here.parent.parent
    return Path.cwd()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def make_literal_text_fixture() -> dict[str, Any]:
    source = """\
function renderCellControls(cell, controls) {
  if (cell.type === "ai" && cell.status === "running") {
    controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));
  }
}
"""
    return {
        "fixture_id": "literal_text_change",
        "mode": "self_contained_evidence_fixture",
        "target_file": TARGET_PATH,
        "task": (
            "Change the visible running AI button label from Stop to Cancel. "
            "Preserve the click handler and preserve that a button element is appended to controls."
        ),
        "files": {
            TARGET_PATH: {
                "content": source,
                "sha256": sha256_text(source),
            }
        },
        "trusted_rules": [],
        "local_probe_results": [],
        "fixture_expectation": (
            "This fixture should be grounded mostly from literal source evidence. "
            "It should not require external API knowledge if the proposed edit only changes the label string."
        ),
    }


def make_semantic_api_fixture() -> dict[str, Any]:
    source = """\
function renderCellControls(cell, controls) {
  if (cell.type === "ai" && cell.status === "running") {
    controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));
  }
}
"""
    return {
        "fixture_id": "semantic_api_claim",
        "mode": "self_contained_evidence_fixture",
        "target_file": TARGET_PATH,
        "task": (
            "Add btn-danger styling to the running AI Stop button. "
            "Preserve the click handler and preserve that a button element is appended to controls."
        ),
        "files": {
            TARGET_PATH: {
                "content": source,
                "sha256": sha256_text(source),
            }
        },
        "trusted_rules": [],
        "local_probe_results": [],
        "fixture_expectation": (
            "This fixture is intentionally harder. If the model relies on browser API behavior "
            "such as class mutation or return values, those claims must be verified by supplied "
            "evidence, a trusted rule, a local probe, or must block generation/promotion."
        ),
    }


def make_evidence_bundle(fixture: str) -> dict[str, Any]:
    if fixture == "literal_text":
        return make_literal_text_fixture()
    if fixture == "semantic_api":
        return make_semantic_api_fixture()
    raise ValueError(f"unknown fixture: {fixture}")


def compact_grounding_schema() -> dict[str, Any]:
    return {
        "mode": "claim_grounding_card",
        "target_file": TARGET_PATH,
        "evidence_exact_text": "exact substring copied from source",
        "intended_change": "specific change to make",
        "preserve": [
            {
                "id": "I1",
                "description": "what must remain true",
                "evidence_exact_text": "exact substring supporting this invariant",
                "critical": True,
            }
        ],
        "claims": [
            {
                "id": "C1",
                "claim": "specific claim used by edit",
                "kind": "source_observation | text_transformation | language_semantics | api_semantics | project_convention | runtime_behavior | transformation_safety",
                "used_by_edit": True,
                "verification_status": "anchored_in_evidence | verified_by_trusted_rule | verified_by_local_probe | unverified | not_needed",
                "if_unverified": "block_generation | block_promotion | allow_with_warning | not_applicable",
            }
        ],
        "uncertainties": [
            {
                "id": "U1",
                "description": "what is unknown",
                "impact": "none | warning | block_generation | block_promotion",
            }
        ],
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior | preservation | regression_guard | syntax_shape | other",
                "kind": "literal_must_contain | literal_must_not_contain | regex_must_match | regex_must_not_match",
                "value": "literal or regex",
                "critical": True,
            }
        ],
        "generation_recommendation": {
            "allowed": True,
            "reason": "why generation may or may not proceed",
        },
    }


def make_grounding_prompt(evidence: dict[str, Any]) -> str:
    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = compact_grounding_schema()

    # Keep this prompt short on purpose. The previous smoke asked for a large
    # dossier and some local models produced corrupted JSON. This is the same
    # protocol compressed into one small card.
    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are the first stage of a repo-edit pipeline. Do not write a patch.
Create a compact claim-grounding card.

Rules:
- evidence_exact_text and preserve[].evidence_exact_text must be exact substrings from SOURCE.
- Every edit-relevant claim needs verification_status.
- If a used_by_edit claim is unverified, if_unverified must block_generation or block_promotion.
- trusted/local verification is only allowed if supplied in evidence; none are supplied here.
- Include at least one new_behavior check and at least one preservation check.
- Supported check kinds: literal_must_contain, literal_must_not_contain, regex_must_match, regex_must_not_match.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

TASK:
{evidence["task"]}

TARGET_FILE:
{target_file}

SOURCE:
{source}
""".strip()


def make_grounding_retry_prompt(evidence: dict[str, Any], bad_raw: str) -> str:
    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = compact_grounding_schema()
    return f"""
Your previous response was invalid JSON. Repair by returning exactly one minified JSON object.
The first character must be {{ and the last character must be }}.
Do not include markdown. Do not include explanations. Do not repeat corrupted text.

Required shape:
{json.dumps(schema, separators=(",", ":"))}

Rules:
- Copy evidence_exact_text from SOURCE exactly.
- Do not use keys not present in the required shape.
- Include one new_behavior check and one preservation check.
- If unsure, set generation_recommendation.allowed=false and include a blocking uncertainty.

TASK:
{evidence["task"]}

TARGET_FILE:
{target_file}

SOURCE:
{source}

INVALID_PREVIOUS_RESPONSE_PREVIEW:
{bad_raw[:1200]}
""".strip()


def make_patch_prompt(evidence: dict[str, Any], card: dict[str, Any]) -> str:
    target_file = evidence["target_file"]
    source = evidence["files"][target_file]["content"]
    schema = {
        "mode": "claim_grounded_patch_proposal",
        "target_file": target_file,
        "patched_source": "full final content for target file",
        "grounding_ids_used": ["I1", "C1", "P1"],
    }

    return f"""
Return exactly one valid JSON object. The first character must be {{ and the last character must be }}.
No markdown. No prose. No comments.

You are the second stage of a repo-edit pipeline.
Propose the full patched source for exactly one file.
The proposal must obey the accepted grounding card and pass its checks.

JSON shape:
{json.dumps(schema, separators=(",", ":"))}

SOURCE:
{source}

ACCEPTED_GROUNDING_CARD:
{json.dumps(card, separators=(",", ":"))}
""".strip()


def sanitize_payload_for_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep Ollama payload diagnostics useful without duplicating full streams or token contexts."""
    sanitized: dict[str, Any] = {"keys": sorted(str(key) for key in payload.keys())}
    for key, value in payload.items():
        if key in {"response", "thinking"} and isinstance(value, str):
            sanitized[f"{key}_length"] = len(value)
            sanitized[f"{key}_preview"] = value[:120]
        elif key == "context" and isinstance(value, list):
            sanitized["context_length"] = len(value)
            sanitized["context_head"] = value[:12]
            sanitized["context_tail"] = value[-12:]
        elif isinstance(value, str) and len(value) > 240:
            sanitized[f"{key}_length"] = len(value)
            sanitized[f"{key}_preview"] = value[:120]
        else:
            sanitized[key] = value
    return sanitized


def parse_think_mode(value: str) -> bool | str | None:
    if value == "omit":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def call_ollama_generate_detailed(
    *,
    model: str,
    prompt: str,
    ollama_url: str,
    timeout_seconds: int,
    num_predict: int,
    format_mode: str,
    think_mode: str,
) -> OllamaGenerateResult:
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0,
            "num_predict": num_predict,
        },
    }

    if format_mode != "none":
        body["format"] = format_mode

    think_value = parse_think_mode(think_mode)
    if think_value is not None:
        body["think"] = think_value

    diagnostics: dict[str, Any] = {
        "request": {
            "model": model,
            "ollama_url": ollama_url,
            "stream": True,
            "format_mode": format_mode,
            "num_predict": num_predict,
            "think_mode": think_mode,
            "prompt_length": len(prompt),
            "prompt_sha256": sha256_text(prompt),
        },
        "http_status": None,
        "http_reason": None,
        "content_type": None,
        "stream_line_count": 0,
        "stream_nonempty_line_count": 0,
        "stream_empty_line_count": 0,
        "json_payload_count": 0,
        "non_json_line_count": 0,
        "payload_key_counts": {},
        "payloads_with_response_key": 0,
        "payloads_with_nonempty_response": 0,
        "response_character_count": 0,
        "payloads_with_thinking_key": 0,
        "payloads_with_nonempty_thinking": 0,
        "thinking_character_count": 0,
        "done_seen": False,
        "done_reason": None,
        "error_payloads": [],
        "non_json_line_previews": [],
        "first_payloads": [],
        "last_payload": None,
    }

    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        ollama_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    response_chunks: list[str] = []
    thinking_chunks: list[str] = []
    stream_lines: list[str] = []

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        diagnostics["http_status"] = getattr(response, "status", None)
        diagnostics["http_reason"] = getattr(response, "reason", None)
        diagnostics["content_type"] = response.headers.get("Content-Type")

        for raw_line in response:
            diagnostics["stream_line_count"] += 1
            line = raw_line.decode("utf-8", errors="replace").strip()
            stream_lines.append(line)

            if not line:
                diagnostics["stream_empty_line_count"] += 1
                continue

            diagnostics["stream_nonempty_line_count"] += 1

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                diagnostics["non_json_line_count"] += 1
                if len(diagnostics["non_json_line_previews"]) < 5:
                    diagnostics["non_json_line_previews"].append(line[:500])
                continue

            if not isinstance(payload, dict):
                diagnostics["non_json_line_count"] += 1
                if len(diagnostics["non_json_line_previews"]) < 5:
                    diagnostics["non_json_line_previews"].append(line[:500])
                continue

            diagnostics["json_payload_count"] += 1
            key_counts = diagnostics["payload_key_counts"]
            for key in payload.keys():
                key_name = str(key)
                key_counts[key_name] = key_counts.get(key_name, 0) + 1

            sanitized = sanitize_payload_for_report(payload)
            if len(diagnostics["first_payloads"]) < 3:
                diagnostics["first_payloads"].append(sanitized)
            diagnostics["last_payload"] = sanitized

            if "error" in payload:
                diagnostics["error_payloads"].append(sanitized)

            if "response" in payload:
                diagnostics["payloads_with_response_key"] += 1
                token = payload.get("response", "")
                if isinstance(token, str):
                    if token:
                        diagnostics["payloads_with_nonempty_response"] += 1
                    diagnostics["response_character_count"] += len(token)
                    response_chunks.append(token)

            if "thinking" in payload:
                diagnostics["payloads_with_thinking_key"] += 1
                token = payload.get("thinking", "")
                if isinstance(token, str):
                    if token:
                        diagnostics["payloads_with_nonempty_thinking"] += 1
                    diagnostics["thinking_character_count"] += len(token)
                    thinking_chunks.append(token)

            if payload.get("done") is True:
                diagnostics["done_seen"] = True
                diagnostics["done_reason"] = payload.get("done_reason")
                break

    response_text = "".join(response_chunks)
    thinking_text = "".join(thinking_chunks)
    diagnostics["joined_response_length"] = len(response_text)
    diagnostics["joined_response_sha256"] = sha256_text(response_text) if response_text else None
    diagnostics["joined_thinking_length"] = len(thinking_text)
    diagnostics["joined_thinking_sha256"] = sha256_text(thinking_text) if thinking_text else None
    diagnostics["stream_lines_sha256"] = sha256_text("\n".join(stream_lines)) if stream_lines else None

    return OllamaGenerateResult(
        text=response_text,
        thinking_text=thinking_text,
        diagnostics=diagnostics,
        stream_lines=stream_lines,
    )


def call_ollama_generate(
    *,
    model: str,
    prompt: str,
    ollama_url: str,
    timeout_seconds: int,
    num_predict: int,
    format_mode: str,
    think_mode: str = "false",
) -> str:
    return call_ollama_generate_detailed(
        model=model,
        prompt=prompt,
        ollama_url=ollama_url,
        timeout_seconds=timeout_seconds,
        num_predict=num_predict,
        format_mode=format_mode,
        think_mode=think_mode,
    ).text

def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < first:
        raise ValueError(f"no JSON object found in model output; raw_preview={text[:300]!r}")

    candidate = text[first : last + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("model output JSON was not an object")
    return parsed


def get_target_content(evidence: dict[str, Any]) -> str:
    target_file = evidence["target_file"]
    return str(evidence["files"][target_file]["content"])


def list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validate_check_object(check: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    blocking_reasons: list[str] = []

    check_id = check.get("id", "<missing>")
    intent = check.get("intent")
    kind = check.get("kind")
    value = check.get("value")
    critical = check.get("critical")

    if not isinstance(check.get("id"), str) or not check.get("id"):
        issues.append("check missing id")
    if intent not in ALLOWED_CHECK_INTENTS:
        issues.append(f"check {check_id!r} has invalid intent {intent!r}")
    if kind not in ALLOWED_CHECK_KINDS:
        issues.append(f"check {check_id!r} has invalid kind {kind!r}")
    if not isinstance(value, str) or value == "":
        issues.append(f"check {check_id!r} missing value")
    if not isinstance(critical, bool):
        issues.append(f"check {check_id!r} critical must be boolean")
    if critical is True and kind not in ALLOWED_CHECK_KINDS:
        blocking_reasons.append(f"critical check {check_id!r} has unsupported kind")

    return issues, blocking_reasons


def validate_grounding_card(card: dict[str, Any] | None, evidence: dict[str, Any]) -> CheckResult:
    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    if not card:
        return CheckResult(False, ["missing grounding card"], [], ["grounding unavailable"])

    source = get_target_content(evidence)
    target_file = evidence["target_file"]

    if card.get("mode") != "claim_grounding_card":
        issues.append("card mode must be claim_grounding_card")

    if card.get("target_file") != target_file:
        issues.append("card target_file does not match evidence target_file")

    evidence_exact_text = card.get("evidence_exact_text")
    if not isinstance(evidence_exact_text, str) or not evidence_exact_text:
        issues.append("card missing evidence_exact_text")
    elif evidence_exact_text not in source:
        issues.append("evidence_exact_text is not an exact substring of source")

    if not isinstance(card.get("intended_change"), str) or not card.get("intended_change", "").strip():
        issues.append("card missing intended_change")

    preserve = list_of_dicts(card.get("preserve"))
    if not preserve:
        issues.append("card must contain preserve invariants")

    preserve_ids: set[str] = set()
    critical_preserve_ids: set[str] = set()

    for invariant in preserve:
        invariant_id = invariant.get("id", "<missing>")
        if not isinstance(invariant.get("id"), str) or not invariant.get("id"):
            issues.append("preserve invariant missing id")
        else:
            preserve_ids.add(invariant["id"])

        if not isinstance(invariant.get("description"), str) or not invariant.get("description", "").strip():
            issues.append(f"preserve invariant {invariant_id!r} missing description")

        invariant_text = invariant.get("evidence_exact_text")
        if not isinstance(invariant_text, str) or not invariant_text:
            issues.append(f"preserve invariant {invariant_id!r} missing evidence_exact_text")
        elif invariant_text not in source:
            issues.append(f"preserve invariant {invariant_id!r} evidence_exact_text is not in source")

        if not isinstance(invariant.get("critical"), bool):
            issues.append(f"preserve invariant {invariant_id!r} critical must be boolean")
        elif invariant.get("critical") is True and isinstance(invariant.get("id"), str):
            critical_preserve_ids.add(invariant["id"])

    claims = list_of_dicts(card.get("claims"))
    if not claims:
        issues.append("card must contain claims")

    trusted_rule_ids = {
        str(rule.get("rule_id"))
        for rule in evidence.get("trusted_rules", [])
        if isinstance(rule, dict) and isinstance(rule.get("rule_id"), str)
    }
    local_probe_ids = {
        str(probe.get("probe_id"))
        for probe in evidence.get("local_probe_results", [])
        if isinstance(probe, dict) and isinstance(probe.get("probe_id"), str)
    }

    for claim in claims:
        claim_id = claim.get("id", "<missing>")
        kind = claim.get("kind")
        used_by_edit = claim.get("used_by_edit")
        status = claim.get("verification_status")
        if_unverified = claim.get("if_unverified")

        if not isinstance(claim.get("id"), str) or not claim.get("id"):
            issues.append("claim missing id")
        if not isinstance(claim.get("claim"), str) or not claim.get("claim", "").strip():
            issues.append(f"claim {claim_id!r} missing claim text")
        if kind not in ALLOWED_CLAIM_KINDS:
            issues.append(f"claim {claim_id!r} has invalid kind {kind!r}")
        if not isinstance(used_by_edit, bool):
            issues.append(f"claim {claim_id!r} used_by_edit must be boolean")
        if status not in ALLOWED_VERIFICATION_STATUSES:
            issues.append(f"claim {claim_id!r} has invalid verification_status {status!r}")
        if if_unverified not in ALLOWED_IF_UNVERIFIED:
            issues.append(f"claim {claim_id!r} has invalid if_unverified {if_unverified!r}")

        if status == "verified_by_trusted_rule":
            rule_id = claim.get("trusted_rule_id")
            if not isinstance(rule_id, str) or rule_id not in trusted_rule_ids:
                issues.append(f"claim {claim_id!r} uses unknown trusted_rule_id {rule_id!r}")

        if status == "verified_by_local_probe":
            probe_id = claim.get("local_probe_id")
            if not isinstance(probe_id, str) or probe_id not in local_probe_ids:
                issues.append(f"claim {claim_id!r} uses unknown local_probe_id {probe_id!r}")

        if status == "unverified" and used_by_edit:
            if if_unverified in {"block_generation", "block_promotion"}:
                blocking_reasons.append(
                    f"claim {claim_id!r} is edit-relevant but unverified and requests {if_unverified}"
                )
            else:
                issues.append(
                    f"claim {claim_id!r} is edit-relevant and unverified but does not block"
                )

    uncertainties = list_of_dicts(card.get("uncertainties"))
    for uncertainty in uncertainties:
        uncertainty_id = uncertainty.get("id", "<missing>")
        impact = uncertainty.get("impact")
        if impact in {"block_generation", "block_promotion"}:
            blocking_reasons.append(f"uncertainty {uncertainty_id!r} has impact {impact}")
        elif impact not in {"none", "warning", "block_generation", "block_promotion"}:
            issues.append(f"uncertainty {uncertainty_id!r} has invalid impact {impact!r}")

    checks = list_of_dicts(card.get("checks"))
    if not checks:
        issues.append("card must contain checks")

    check_intents: set[str] = set()
    critical_check_values = " ".join(str(check.get("value", "")) for check in checks if check.get("critical") is True)

    for check in checks:
        check_issues, check_blocks = validate_check_object(check)
        issues.extend(check_issues)
        blocking_reasons.extend(check_blocks)
        if isinstance(check.get("intent"), str):
            check_intents.add(check["intent"])

    if "new_behavior" not in check_intents:
        issues.append("checks must include a new_behavior check")
    if "preservation" not in check_intents:
        issues.append("checks must include a preservation check")

    # Generic guard: a critical preserve invariant needs some critical check.
    # We do not require semantic understanding here; we require the model to give
    # a mechanically executable check for what it says must be preserved.
    if critical_preserve_ids and not critical_check_values:
        blocking_reasons.append("critical preservation invariant exists but no critical check was provided")

    recommendation = card.get("generation_recommendation")
    if not isinstance(recommendation, dict):
        issues.append("card missing generation_recommendation")
    else:
        allowed = recommendation.get("allowed")
        if not isinstance(allowed, bool):
            issues.append("generation_recommendation.allowed must be boolean")
        elif allowed is False:
            blocking_reasons.append("grounding card recommends blocking generation")
        if not isinstance(recommendation.get("reason"), str) or not recommendation.get("reason", "").strip():
            issues.append("generation_recommendation.reason is required")

    ok = not issues and not blocking_reasons
    return CheckResult(ok, issues, warnings, blocking_reasons)


def apply_patch_acceptance_checks(card: dict[str, Any], patched_source: str) -> CheckResult:
    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    checks = list_of_dicts(card.get("checks"))

    for check in checks:
        check_id = str(check.get("id", "<missing>"))
        kind = check.get("kind")
        value = check.get("value")
        critical = check.get("critical") is True

        if not isinstance(value, str):
            issues.append(f"check {check_id!r} value is not a string")
            continue

        passed = False
        error: str | None = None

        try:
            if kind == "literal_must_contain":
                passed = value in patched_source
            elif kind == "literal_must_not_contain":
                passed = value not in patched_source
            elif kind == "regex_must_match":
                passed = re.search(value, patched_source, flags=re.MULTILINE | re.DOTALL) is not None
            elif kind == "regex_must_not_match":
                passed = re.search(value, patched_source, flags=re.MULTILINE | re.DOTALL) is None
            else:
                error = f"unsupported check kind {kind!r}"
        except re.error as exc:
            error = f"invalid regex: {exc}"

        if error:
            issues.append(f"check {check_id!r} error: {error}")
            if critical:
                blocking_reasons.append(f"critical check {check_id!r} could not run")
        elif not passed:
            message = f"check {check_id!r} failed"
            if critical:
                blocking_reasons.append(message)
            else:
                warnings.append(message)

    return CheckResult(not issues and not blocking_reasons, issues, warnings, blocking_reasons)


def make_unified_diff(old: str, new: str, path: str = TARGET_PATH) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def validate_patch_proposal(
    *,
    proposal: dict[str, Any] | None,
    card: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[CheckResult, str]:
    issues: list[str] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    if not proposal:
        return CheckResult(False, ["missing patch proposal"], [], ["patch proposal unavailable"]), ""

    if proposal.get("mode") != "claim_grounded_patch_proposal":
        issues.append("patch proposal mode must be claim_grounded_patch_proposal")

    if proposal.get("target_file") != evidence.get("target_file"):
        issues.append("patch proposal target_file does not match evidence target_file")

    patched_source = proposal.get("patched_source")
    if not isinstance(patched_source, str) or not patched_source:
        issues.append("patch proposal missing patched_source")
        return CheckResult(False, issues, warnings, blocking_reasons), ""

    old_source = get_target_content(evidence)
    diff_text = make_unified_diff(old_source, patched_source, str(evidence["target_file"]))

    if patched_source == old_source:
        blocking_reasons.append("patch proposal makes no change")

    declared_ids = proposal.get("grounding_ids_used")
    if not isinstance(declared_ids, list) or not declared_ids:
        warnings.append("patch proposal did not list grounding_ids_used")

    check_result = apply_patch_acceptance_checks(card, patched_source)
    issues.extend(check_result.issues)
    warnings.extend(check_result.warnings or [])
    blocking_reasons.extend(check_result.blocking_reasons or [])

    return CheckResult(not issues and not blocking_reasons, issues, warnings, blocking_reasons), diff_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=MODE)
    parser.add_argument("--model", default=None, help="Ollama model name. Defaults to OLLAMA_MODEL.")
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434/api/generate",
        help="Ollama /api/generate endpoint.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--num-predict", type=int, default=500)
    parser.add_argument(
        "--format-mode",
        choices=["json", "none"],
        default="none",
        help="Ollama format option for the first grounding call. Default none because some models degrade in json mode.",
    )
    parser.add_argument(
        "--retry-format-mode",
        choices=["json", "none"],
        default="none",
        help="Ollama format option for the grounding retry call.",
    )
    parser.add_argument(
        "--think-mode",
        choices=["omit", "false", "true", "low", "medium", "high"],
        default="false",
        help=(
            "Ollama think option for grounding calls. Default false prevents thinking-only "
            "streams from consuming the output budget before a final response is produced."
        ),
    )
    parser.add_argument(
        "--fixture",
        choices=["literal_text", "semantic_api"],
        default="literal_text",
        help="literal_text is the default general-protocol fixture; semantic_api exercises safe blocking.",
    )
    parser.add_argument(
        "--skip-patch-proposal",
        action="store_true",
        help="Only call the model for the grounding card.",
    )
    parser.add_argument(
        "--require-generation-allowed",
        action="store_true",
        help="Exit nonzero unless the grounding card allows patch proposal generation.",
    )
    parser.add_argument(
        "--require-promotable",
        action="store_true",
        help="Exit nonzero unless the patch proposal passes all checks.",
    )
    parser.add_argument(
        "--offline-self-check",
        action="store_true",
        help="Run deterministic verifier checks without calling Ollama.",
    )
    return parser.parse_args()


def make_good_offline_card(evidence: dict[str, Any]) -> dict[str, Any]:
    source = get_target_content(evidence)
    edit_text = 'controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));'
    assert edit_text in source
    return {
        "mode": "claim_grounding_card",
        "target_file": evidence["target_file"],
        "evidence_exact_text": edit_text,
        "intended_change": "Change the visible label Stop to Cancel while preserving the click handler and append behavior.",
        "preserve": [
            {
                "id": "I1",
                "description": "The click handler call must remain present.",
                "evidence_exact_text": "stopChatConsoleAiRequest(cell.id)",
                "critical": True,
            },
            {
                "id": "I2",
                "description": "The code must still append a button/control to controls.",
                "evidence_exact_text": "controls.append",
                "critical": True,
            },
        ],
        "claims": [
            {
                "id": "C1",
                "claim": "The requested label change is a literal text transformation in the source.",
                "kind": "text_transformation",
                "used_by_edit": True,
                "verification_status": "anchored_in_evidence",
                "if_unverified": "not_applicable",
            }
        ],
        "uncertainties": [],
        "checks": [
            {
                "id": "P1",
                "intent": "new_behavior",
                "kind": "literal_must_contain",
                "value": 'chatConsoleButton("Cancel"',
                "critical": True,
            },
            {
                "id": "P2",
                "intent": "preservation",
                "kind": "literal_must_contain",
                "value": "stopChatConsoleAiRequest(cell.id)",
                "critical": True,
            },
            {
                "id": "P3",
                "intent": "preservation",
                "kind": "literal_must_contain",
                "value": "controls.append",
                "critical": True,
            },
        ],
        "generation_recommendation": {
            "allowed": True,
            "reason": "All edit-relevant claims are anchored in provided evidence and checks are mechanical.",
        },
    }


def make_blocking_offline_card(evidence: dict[str, Any]) -> dict[str, Any]:
    good = make_good_offline_card(evidence)
    good["claims"] = [
        {
            "id": "C1",
            "claim": "Some external API behavior is required but not verified.",
            "kind": "api_semantics",
            "used_by_edit": True,
            "verification_status": "unverified",
            "if_unverified": "block_generation",
        }
    ]
    good["generation_recommendation"] = {
        "allowed": False,
        "reason": "A critical API claim is unverified.",
    }
    return good


def run_offline_self_check(repo_root: Path) -> tuple[dict[str, Any], int]:
    evidence = make_evidence_bundle("literal_text")
    good_card = make_good_offline_card(evidence)
    blocking_card = make_blocking_offline_card(evidence)

    good_result = validate_grounding_card(good_card, evidence)
    blocking_result = validate_grounding_card(blocking_card, evidence)

    patched_source = get_target_content(evidence).replace('chatConsoleButton("Stop"', 'chatConsoleButton("Cancel"', 1)
    patch_result, diff_text = validate_patch_proposal(
        proposal={
            "mode": "claim_grounded_patch_proposal",
            "target_file": evidence["target_file"],
            "patched_source": patched_source,
            "grounding_ids_used": ["I1", "I2", "C1", "P1", "P2", "P3"],
        },
        card=good_card,
        evidence=evidence,
    )

    report = {
        "mode": MODE,
        "offline_self_check": True,
        "ok": good_result.ok and (not blocking_result.ok) and bool(blocking_result.blocking_reasons) and patch_result.ok,
        "good_card_result": good_result.as_dict(),
        "blocking_card_result": blocking_result.as_dict(),
        "patch_result": patch_result.as_dict(),
        "patch_diff_sha256": sha256_text(diff_text) if diff_text else None,
    }

    output_root = (
        repo_root
        / "debug_assets"
        / "rag_generated_editor_claim_grounding"
        / f"offline_self_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    write_json(output_root / "final_report.json", report)

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {output_root / 'final_report.json'}")

    return report, 0 if report["ok"] else 1


def main() -> int:
    args = parse_args()

    repo_root = detect_repo_root()

    if args.offline_self_check:
        _, exit_code = run_offline_self_check(repo_root)
        return exit_code

    model = args.model or os.environ.get("OLLAMA_MODEL")
    if not model:
        print("ERROR: provide --model or set OLLAMA_MODEL", file=sys.stderr)
        return 2

    real_target = repo_root / TARGET_PATH
    real_hash_before = file_sha256(real_target)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = (
        repo_root
        / "debug_assets"
        / "rag_generated_editor_claim_grounding"
        / f"claim_grounding_smoke_{run_id}"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    event_log: list[str] = ["evidence_loaded"]

    evidence = make_evidence_bundle(args.fixture)
    write_json(output_root / "00_evidence_bundle.json", evidence)

    grounding_prompt = make_grounding_prompt(evidence)
    write_text(output_root / "01_grounding_model_request.txt", grounding_prompt)

    grounding_raw = ""
    grounding_card: dict[str, Any] | None = None
    grounding_parse_error: str | None = None
    grounding_call_error: str | None = None
    grounding_elapsed_seconds: float | None = None
    grounding_attempts: list[dict[str, Any]] = []
    selected_grounding_raw_attempt = "none"
    grounding_result = CheckResult(False, ["grounding model call did not complete"], [], ["grounding unavailable"])

    try:
        event_log.append("grounding_model_call_started")
        started = time.time()
        first_call = call_ollama_generate_detailed(
            model=model,
            prompt=grounding_prompt,
            ollama_url=args.ollama_url,
            timeout_seconds=args.timeout_seconds,
            num_predict=args.num_predict,
            format_mode=args.format_mode,
            think_mode=args.think_mode,
        )
        first_raw = first_call.text
        grounding_elapsed_seconds = round(time.time() - started, 3)
        event_log.append("grounding_model_call_completed")
        if not first_raw:
            event_log.append("grounding_initial_raw_empty")
            if first_call.diagnostics.get("stream_nonempty_line_count", 0) == 0:
                event_log.append("grounding_initial_stream_no_nonempty_lines")
            elif first_call.diagnostics.get("payloads_with_nonempty_response", 0) == 0:
                event_log.append("grounding_initial_stream_no_response_tokens")
        if not first_raw and first_call.thinking_text:
            event_log.append("grounding_initial_thinking_seen_without_response")
        write_text(output_root / "02_grounding_model_raw.txt", first_raw)
        write_text(output_root / "02a_grounding_model_thinking.txt", first_call.thinking_text)
        write_text(output_root / "02_grounding_model_stream.jsonl", "\n".join(first_call.stream_lines))
        write_json(output_root / "02_grounding_model_transport.json", first_call.diagnostics)

        grounding_raw = first_raw
        selected_grounding_raw_attempt = "initial"

        try:
            grounding_card = extract_json_object(first_raw)
            write_json(output_root / "03_grounding_card.json", grounding_card)
            grounding_attempts.append(
                {
                    "name": "initial",
                    "parsed": True,
                    "parse_error": None,
                    "transport": first_call.diagnostics,
                    "thinking": raw_summary(first_call.thinking_text, limit=300),
                    **raw_summary(first_raw),
                }
            )
            event_log.append("grounding_json_parsed")
        except Exception as exc:
            initial_parse_error = repr(exc)
            grounding_parse_error = initial_parse_error
            grounding_attempts.append(
                {
                    "name": "initial",
                    "parsed": False,
                    "parse_error": initial_parse_error,
                    "transport": first_call.diagnostics,
                    "thinking": raw_summary(first_call.thinking_text, limit=300),
                    **raw_summary(first_raw),
                }
            )
            event_log.append("grounding_json_parse_failed")

            retry_prompt = make_grounding_retry_prompt(evidence, first_raw)
            write_text(output_root / "02b_grounding_retry_request.txt", retry_prompt)

            event_log.append("grounding_json_retry_started")
            retry_call = call_ollama_generate_detailed(
                model=model,
                prompt=retry_prompt,
                ollama_url=args.ollama_url,
                timeout_seconds=args.timeout_seconds,
                num_predict=args.num_predict,
                format_mode=args.retry_format_mode,
                think_mode=args.think_mode,
            )
            retry_raw = retry_call.text
            if not retry_raw and retry_call.thinking_text:
                event_log.append("grounding_retry_thinking_seen_without_response")
            write_text(output_root / "02c_grounding_retry_raw.txt", retry_raw)
            write_text(output_root / "02f_grounding_retry_thinking.txt", retry_call.thinking_text)
            write_text(output_root / "02d_grounding_retry_stream.jsonl", "\n".join(retry_call.stream_lines))
            write_json(output_root / "02e_grounding_retry_transport.json", retry_call.diagnostics)
            event_log.append("grounding_json_retry_completed")
            if not retry_raw:
                event_log.append("grounding_retry_raw_empty")
                if retry_call.diagnostics.get("stream_nonempty_line_count", 0) == 0:
                    event_log.append("grounding_retry_stream_no_nonempty_lines")
                elif retry_call.diagnostics.get("payloads_with_nonempty_response", 0) == 0:
                    event_log.append("grounding_retry_stream_no_response_tokens")

            try:
                grounding_card = extract_json_object(retry_raw)
                write_json(output_root / "03_grounding_card.json", grounding_card)
                grounding_raw = retry_raw
                selected_grounding_raw_attempt = "retry"
                grounding_parse_error = None
                grounding_attempts.append(
                    {
                        "name": "retry",
                        "parsed": True,
                        "parse_error": None,
                        "transport": retry_call.diagnostics,
                        "thinking": raw_summary(retry_call.thinking_text, limit=300),
                        **raw_summary(retry_raw),
                    }
                )
                event_log.append("grounding_json_retry_parsed")
            except Exception as retry_exc:
                retry_parse_error = repr(retry_exc)
                grounding_attempts.append(
                    {
                        "name": "retry",
                        "parsed": False,
                        "parse_error": retry_parse_error,
                        "transport": retry_call.diagnostics,
                        "thinking": raw_summary(retry_call.thinking_text, limit=300),
                        **raw_summary(retry_raw),
                    }
                )
                if retry_raw:
                    grounding_raw = retry_raw
                    selected_grounding_raw_attempt = "retry"
                else:
                    grounding_raw = first_raw
                    selected_grounding_raw_attempt = "initial"
                grounding_parse_error = (
                    "all grounding JSON parse attempts failed; "
                    f"initial={initial_parse_error}; retry={retry_parse_error}"
                )
                event_log.append("grounding_json_retry_parse_failed")

        grounding_result = validate_grounding_card(grounding_card, evidence)
        if grounding_result.ok:
            event_log.append("grounding_verified")
        else:
            event_log.append("grounding_rejected_or_blocking")

    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
        grounding_call_error = repr(exc)
        event_log.append("grounding_model_call_failed")

    grounding_report = {
        "ok": grounding_result.ok,
        "issues": grounding_result.issues,
        "warnings": grounding_result.warnings or [],
        "blocking_reasons": grounding_result.blocking_reasons or [],
        "parse_error": grounding_parse_error,
        "call_error": grounding_call_error,
        "elapsed_seconds": grounding_elapsed_seconds,
        "selected_raw_attempt": selected_grounding_raw_attempt,
        "attempts": grounding_attempts,
    }
    grounding_report.update(raw_summary(grounding_raw))
    write_json(output_root / "04_grounding_verification.json", grounding_report)

    generation_allowed = grounding_result.ok
    patch_raw = ""
    patch_proposal: dict[str, Any] | None = None
    patch_parse_error: str | None = None
    patch_call_error: str | None = None
    patch_elapsed_seconds: float | None = None
    patch_result = CheckResult(False, ["patch proposal was not run"], [], ["patch proposal unavailable"])
    diff_text = ""

    if args.skip_patch_proposal:
        event_log.append("patch_proposal_model_call_skipped_by_flag")
        patch_result = CheckResult(True, [], ["patch proposal skipped by flag"], [])
    elif not generation_allowed:
        event_log.append("patch_proposal_model_call_blocked_by_grounding")
    else:
        event_log.append("patch_proposal_model_call_allowed")
        patch_prompt = make_patch_prompt(evidence, grounding_card or {})
        write_text(output_root / "05_patch_proposal_model_request.txt", patch_prompt)

        try:
            event_log.append("patch_proposal_model_call_started")
            started = time.time()
            patch_call = call_ollama_generate_detailed(
                model=model,
                prompt=patch_prompt,
                ollama_url=args.ollama_url,
                timeout_seconds=args.timeout_seconds,
                num_predict=args.num_predict,
                format_mode=args.format_mode,
                think_mode=args.think_mode,
            )
            patch_raw = patch_call.text
            patch_elapsed_seconds = round(time.time() - started, 3)
            event_log.append("patch_proposal_model_call_completed")
            write_text(output_root / "06_patch_proposal_model_raw.txt", patch_raw)
            write_text(output_root / "06a_patch_proposal_model_thinking.txt", patch_call.thinking_text)
            write_text(output_root / "06b_patch_proposal_model_stream.jsonl", "\n".join(patch_call.stream_lines))
            write_json(output_root / "06c_patch_proposal_transport.json", patch_call.diagnostics)
            if not patch_raw and patch_call.thinking_text:
                event_log.append("patch_proposal_thinking_seen_without_response")
            if not patch_raw:
                event_log.append("patch_proposal_raw_empty")
                if patch_call.diagnostics.get("stream_nonempty_line_count", 0) == 0:
                    event_log.append("patch_proposal_stream_no_nonempty_lines")
                elif patch_call.diagnostics.get("payloads_with_nonempty_response", 0) == 0:
                    event_log.append("patch_proposal_stream_no_response_tokens")

            try:
                patch_proposal = extract_json_object(patch_raw)
                write_json(output_root / "07_patch_proposal.json", patch_proposal)
                event_log.append("patch_proposal_json_parsed")
            except Exception as exc:
                patch_parse_error = repr(exc)
                event_log.append("patch_proposal_json_parse_failed")

            patch_result, diff_text = validate_patch_proposal(
                proposal=patch_proposal,
                card=grounding_card or {},
                evidence=evidence,
            )

            if diff_text:
                write_text(output_root / "08_patch_proposal.diff", diff_text)

            if patch_result.ok:
                event_log.append("patch_proposal_verified_against_grounding")
            else:
                event_log.append("patch_proposal_rejected_against_grounding")

        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            patch_call_error = repr(exc)
            event_log.append("patch_proposal_model_call_failed")

    patch_report = {
        "skipped": bool(args.skip_patch_proposal),
        "ok": patch_result.ok,
        "issues": patch_result.issues,
        "warnings": patch_result.warnings or [],
        "blocking_reasons": patch_result.blocking_reasons or [],
        "parse_error": patch_parse_error,
        "call_error": patch_call_error,
        "elapsed_seconds": patch_elapsed_seconds,
        "diff_sha256": sha256_text(diff_text) if diff_text else None,
    }
    patch_report.update(raw_summary(patch_raw))
    write_json(output_root / "09_patch_proposal_verification.json", patch_report)

    real_hash_after = file_sha256(real_target)
    real_repo_modified = real_hash_before != real_hash_after

    promotable = bool(generation_allowed and not args.skip_patch_proposal and patch_result.ok)
    safe_block = bool(
        grounding_card is not None
        and grounding_parse_error is None
        and grounding_call_error is None
        and not generation_allowed
        and bool(grounding_result.blocking_reasons)
        and not grounding_result.issues
    )

    stage_order_ok = True
    if "patch_proposal_model_call_started" in event_log:
        stage_order_ok = (
            "grounding_verified" in event_log
            and event_log.index("grounding_verified") < event_log.index("patch_proposal_model_call_started")
        )

    protocol_ok = (
        grounding_card is not None
        and grounding_parse_error is None
        and grounding_call_error is None
        and stage_order_ok
        and not real_repo_modified
        and (generation_allowed or safe_block)
    )

    if args.require_generation_allowed and not generation_allowed:
        protocol_ok = False

    if args.require_promotable and not promotable:
        protocol_ok = False

    report = {
        "mode": MODE,
        "ok": protocol_ok,
        "model": model,
        "ollama_url": args.ollama_url,
        "fixture": args.fixture,
        "format_mode": args.format_mode,
        "retry_format_mode": args.retry_format_mode,
        "think_mode": args.think_mode,
        "num_predict": args.num_predict,
        "external_model_dependency": True,
        "target_file": TARGET_PATH,
        "event_log": event_log,
        "stage_order_ok": stage_order_ok,
        "grounding_valid_for_generation": grounding_result.ok,
        "generation_allowed": generation_allowed,
        "safe_block": safe_block,
        "promotable": promotable,
        "claim_grounding_policy": {
            "source_evidence_must_be_exact": True,
            "edit_relevant_claims_must_have_verification_status": True,
            "unverified_edit_relevant_claims_must_block": True,
            "trusted_rules_must_be_supplied_in_evidence": True,
            "local_probes_must_be_supplied_in_evidence": True,
            "patch_proposal_blocked_unless_grounding_verified": True,
            "patch_proposal_must_satisfy_grounding_acceptance_checks": True,
            "compact_card_used_to_avoid_giant_dossier_generation": True,
        },
        "grounding": grounding_report,
        "patch_proposal": patch_report,
        "generated_editor_real_repo_execution": False,
        "real_repo_target_exists": real_target.exists(),
        "real_repo_modified": real_repo_modified,
        "real_repo_hash_before": real_hash_before,
        "real_repo_hash_after": real_hash_after,
        "output_root": str(output_root),
    }

    write_json(output_root / "final_report.json", report)

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {output_root / 'final_report.json'}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
