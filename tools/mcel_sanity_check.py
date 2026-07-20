#!/usr/bin/env python3
"""Static sanity checks for MCEL docs, specs, and generated browser contracts.

The sanity checker sits above ``mcel_requirements_registry.py``.  The registry
answers "can the MCEL blocks be parsed?" while this checker answers "do the
parsed contracts still hang together as a coherent specification system?"

Version 1 is intentionally deterministic and static.  It does not inspect live
browser layout; that remains the job of ``MCEL.diagnose(appId)``.  Instead it
checks documentation coverage, semantic app-form primitive adoption, runtime
check linkage, diagnostic code spelling, and whether the generated browser
registry is fresh relative to the docs-derived payload.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.mcel_requirements_registry import (  # noqa: E402
    RegistryBlock,
    RequirementsRegistry,
    build_lab_payload,
    build_registry,
    repo_root_from_here,
)


SANITY_VERSION = "mcel-sanity-check-v1"

EXPECTED_PARSED_DOCS = {
    "pretty_docs/mcel-calculator-requirements.md",
    "pretty_docs/mcel-code-editor-requirements.md",
    "pretty_docs/mcel-file-explorer-requirements.md",
    "pretty_docs/mcel-git-tools-requirements.md",
    "pretty_docs/mcel-website-builder-requirements.md",
    "pretty_docs/mcel-requirements-language.md",
}

CORE_APP_REQUIRED_BLOCKS = {
    "mcel-app",
    "mcel-use-case",
    "mcel-region",
    "mcel-requirement",
    "mcel-intent",
    "mcel-runtime-check",
}

SEMANTIC_FORM_PRIMITIVES = {
    "subject",
    "action",
    "work-surface",
    "context",
    "feedback",
    "constraint",
    "transient",
    "interruption",
}

PHYSICAL_LAYOUT_TERMS = re.compile(
    r"\b("
    r"left|right|top|bottom|sidebar|side\s+bar|pane|rail|drawer|"
    r"top-right|top-left|bottom-right|bottom-left|floating\s+widget"
    r")\b",
    re.IGNORECASE,
)

DIAGNOSTIC_CODE_PATTERN = re.compile(r"\bcode\s*:\s*['\"]([A-Za-z0-9_.-]+)['\"]")
NORMALIZED_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True)
class SanityIssue:
    severity: str
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    block_id: str | None = None
    block_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "block_id": self.block_id,
            "block_type": self.block_type,
        }


@dataclass
class SanityReport:
    repo_root: Path
    strict: bool = False
    registry_summary: dict[str, Any] = field(default_factory=dict)
    issues: list[SanityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[SanityIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[SanityIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def infos(self) -> list[SanityIssue]:
        return [issue for issue in self.issues if issue.severity == "info"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
        block_id: str | None = None,
        block_type: str | None = None,
    ) -> None:
        self.issues.append(
            SanityIssue(
                severity=severity,
                code=code,
                message=message,
                file=file,
                line=line,
                block_id=block_id,
                block_type=block_type,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        issue_counts = Counter(issue.severity for issue in self.issues)
        return {
            "schema": SANITY_VERSION,
            "repo_root": str(self.repo_root),
            "strict": self.strict,
            "valid": self.valid,
            "counts": {
                "errors": issue_counts.get("error", 0),
                "warnings": issue_counts.get("warning", 0),
                "infos": issue_counts.get("info", 0),
            },
            "registry_summary": self.registry_summary,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _blocks_by_app(registry: RequirementsRegistry) -> dict[str, list[RegistryBlock]]:
    grouped: dict[str, list[RegistryBlock]] = defaultdict(list)
    for block in registry.blocks:
        if block.block_type == "mcel-app":
            grouped[block.block_id].append(block)
        elif block.app:
            grouped[block.app].append(block)
    return dict(grouped)


def _blocks_by_type(blocks: Iterable[RegistryBlock]) -> dict[str, list[RegistryBlock]]:
    grouped: dict[str, list[RegistryBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.block_type].append(block)
    return dict(grouped)


def _list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _check_registry_health(report: SanityReport, registry: RequirementsRegistry) -> None:
    if registry.errors:
        for issue in registry.errors:
            report.add(
                "error",
                f"registry.{issue.code}",
                issue.message,
                file=issue.file,
                line=issue.line,
                block_id=issue.block_id,
                block_type=issue.block_type,
            )
    if registry.warnings:
        severity = "error" if report.strict else "warning"
        for issue in registry.warnings:
            report.add(
                severity,
                f"registry.{issue.code}",
                issue.message,
                file=issue.file,
                line=issue.line,
                block_id=issue.block_id,
                block_type=issue.block_type,
            )


def _check_expected_docs(report: SanityReport, registry: RequirementsRegistry) -> None:
    parsed_docs = {block.source_file for block in registry.blocks}
    for expected in sorted(EXPECTED_PARSED_DOCS):
        if expected not in parsed_docs:
            report.add(
                "error",
                "missing-parsed-doc",
                f"Expected MCEL document {expected} to contribute parseable blocks.",
                file=expected,
            )

    docs_root = report.repo_root / "pretty_docs"
    for path in sorted(docs_root.glob("mcel-*.md")):
        rel_path = _relative_path(path, report.repo_root)
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "```mcel-" not in text:
            # This is adoption evidence, not a v1 failure. Several existing MCEL
            # narrative docs are still prose-only.
            report.add(
                "info",
                "prose-only-mcel-doc",
                "MCEL-named document has no machine-readable MCEL blocks yet.",
                file=rel_path,
            )


def _check_app_contract_coverage(report: SanityReport, registry: RequirementsRegistry) -> None:
    for app_id, app_blocks in sorted(_blocks_by_app(registry).items()):
        by_type = _blocks_by_type(app_blocks)
        if "mcel-app" not in by_type:
            report.add(
                "error",
                "app-without-header-contract",
                f"Blocks are scoped to app {app_id!r}, but no mcel-app header contract exists.",
                block_id=app_id,
            )
            continue

        for block_type in sorted(CORE_APP_REQUIRED_BLOCKS):
            if not by_type.get(block_type):
                report.add(
                    "error",
                    "missing-core-app-block-family",
                    f"App {app_id!r} is missing required block family {block_type}.",
                    block_id=app_id,
                    block_type=block_type,
                )

        runtime_checks = by_type.get("mcel-runtime-check", [])
        has_primary_runtime_surface = any(
            str(block.fields.get("primary_surface_id", "")).strip()
            or str(block.fields.get("host_selector", "")).strip()
            for block in runtime_checks
        )
        work_surface_primitives = [
            block
            for block in by_type.get("mcel-form-primitive", [])
            if str(block.fields.get("primitive", "")).strip() == "work-surface"
        ]
        has_primary_form_surface = any(
            "primary" in "\n".join(
                [
                    str(block.fields.get("meaning", "")),
                    "\n".join(_list_values(block.fields.get("relationships"))),
                    "\n".join(_list_values(block.fields.get("constraints"))),
                    str(block.fields.get("authority", "")),
                ]
            ).lower()
            for block in work_surface_primitives
        )
        if not has_primary_runtime_surface and not has_primary_form_surface:
            report.add(
                "error",
                "missing-primary-surface-evidence",
                f"App {app_id!r} has no primary runtime surface or primary work-surface primitive.",
                block_id=app_id,
            )

        form_primitives = by_type.get("mcel-form-primitive", [])
        if not form_primitives:
            report.add(
                "warning",
                "missing-form-primitives",
                (
                    f"App {app_id!r} has no semantic form primitives yet; layout/runtime checks "
                    "must infer meaning from regions until primitives are added."
                ),
                block_id=app_id,
            )
            continue

        primitive_kinds = {
            str(block.fields.get("primitive", "")).strip()
            for block in form_primitives
            if str(block.fields.get("primitive", "")).strip()
        }
        if "work-surface" not in primitive_kinds:
            report.add(
                "warning",
                "missing-work-surface-primitive",
                f"App {app_id!r} has form primitives but no work-surface primitive.",
                block_id=app_id,
            )
        if "feedback" not in primitive_kinds:
            report.add(
                "warning",
                "missing-feedback-primitive",
                f"App {app_id!r} has form primitives but no feedback primitive.",
                block_id=app_id,
            )


def _check_form_primitives(report: SanityReport, registry: RequirementsRegistry) -> None:
    for block in registry.blocks:
        if block.block_type != "mcel-form-primitive":
            continue

        primitive = str(block.fields.get("primitive", "")).strip()
        if primitive not in SEMANTIC_FORM_PRIMITIVES:
            report.add(
                "error",
                "unknown-form-primitive",
                f"Form primitive {block.block_id!r} uses unknown primitive kind {primitive!r}.",
                file=block.source_file,
                line=block.start_line,
                block_id=block.block_id,
                block_type=block.block_type,
            )

        for field_name in ("relationships", "constraints"):
            values = block.fields.get(field_name)
            if not isinstance(values, list) or not values:
                report.add(
                    "error",
                    "empty-form-primitive-list",
                    f"Form primitive {block.block_id!r} must have a non-empty {field_name} list.",
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )

        # Physical nouns are allowed in projection/implementation sections, but they
        # should be rare inside semantic primitive blocks. Warn so spec authors notice
        # when primitives start drifting back into layout slots.
        match = PHYSICAL_LAYOUT_TERMS.search(block.raw)
        if match:
            report.add(
                "warning",
                "physical-layout-term-in-primitive",
                (
                    f"Form primitive {block.block_id!r} mentions physical layout term "
                    f"{match.group(0)!r}; keep primitives semantic unless this is an "
                    "explicit projection example."
                ),
                file=block.source_file,
                line=block.start_line,
                block_id=block.block_id,
                block_type=block.block_type,
            )


def _check_runtime_linkage(report: SanityReport, registry: RequirementsRegistry) -> None:
    grouped = _blocks_by_app(registry)
    for app_id, blocks in sorted(grouped.items()):
        by_type = _blocks_by_type(blocks)
        primitives = by_type.get("mcel-form-primitive", [])
        if not primitives:
            continue

        primitive_text = "\n".join(
            "\n".join(
                [
                    block.block_id,
                    str(block.fields.get("primitive", "")),
                    str(block.fields.get("meaning", "")),
                    "\n".join(_list_values(block.fields.get("relationships"))),
                    "\n".join(_list_values(block.fields.get("constraints"))),
                ]
            ).lower()
            for block in primitives
        )
        for runtime_check in by_type.get("mcel-runtime-check", []):
            check_text = "\n".join(
                [
                    str(runtime_check.fields.get("check", "")),
                    str(runtime_check.fields.get("focus", "")),
                    "\n".join(_list_values(runtime_check.fields.get("observes"))),
                    "\n".join(_list_values(runtime_check.fields.get("expects"))),
                    "\n".join(_list_values(runtime_check.fields.get("overlay_policy"))),
                ]
            ).lower()

            if "primary" in check_text and "work-surface" not in primitive_text:
                report.add(
                    "warning",
                    "runtime-primary-check-without-work-surface-primitive",
                    f"Runtime check {runtime_check.block_id!r} references primary behavior without a work-surface primitive.",
                    file=runtime_check.source_file,
                    line=runtime_check.start_line,
                    block_id=runtime_check.block_id,
                    block_type=runtime_check.block_type,
                )
            if ("overlay" in check_text or "transient" in check_text) and "transient" not in primitive_text:
                report.add(
                    "warning",
                    "runtime-overlay-check-without-transient-primitive",
                    f"Runtime check {runtime_check.block_id!r} references overlay/transient policy without a transient primitive.",
                    file=runtime_check.source_file,
                    line=runtime_check.start_line,
                    block_id=runtime_check.block_id,
                    block_type=runtime_check.block_type,
                )
            if "feedback" in check_text and "feedback" not in primitive_text:
                report.add(
                    "warning",
                    "runtime-feedback-check-without-feedback-primitive",
                    f"Runtime check {runtime_check.block_id!r} references feedback behavior without a feedback primitive.",
                    file=runtime_check.source_file,
                    line=runtime_check.start_line,
                    block_id=runtime_check.block_id,
                    block_type=runtime_check.block_type,
                )


def _extract_browser_payload(js_text: str) -> dict[str, Any]:
    marker = "const PAYLOAD = Object.freeze("
    marker_index = js_text.find(marker)
    if marker_index < 0:
        raise ValueError("Could not find `const PAYLOAD = Object.freeze(` in browser registry.")
    open_brace = js_text.find("{", marker_index)
    if open_brace < 0:
        raise ValueError("Could not find JSON payload object in browser registry.")

    depth = 0
    end_index: int | None = None
    for index in range(open_brace, len(js_text)):
        char = js_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end_index = index + 1
                break
    if end_index is None:
        raise ValueError("Browser registry payload object is not balanced.")

    return json.loads(js_text[open_brace:end_index])


def _normalise_payload_for_freshness(payload: dict[str, Any]) -> dict[str, Any]:
    normalised = json.loads(json.dumps(payload, sort_keys=True))

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "repo_root" in value:
                value["repo_root"] = "<repo-root>"
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(normalised)
    return normalised


def _check_browser_registry_freshness(report: SanityReport, registry: RequirementsRegistry) -> None:
    registry_js = report.repo_root / "main_computer/web/applications/scripts/mcel-requirements-registry.js"
    if not registry_js.exists():
        report.add(
            "error",
            "missing-browser-requirements-registry",
            "Generated browser requirements registry is missing.",
            file=_relative_path(registry_js, report.repo_root),
        )
        return

    try:
        actual_payload = _extract_browser_payload(registry_js.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report.add(
            "error",
            "unreadable-browser-requirements-registry",
            f"Could not parse generated browser requirements registry payload: {exc}",
            file=_relative_path(registry_js, report.repo_root),
        )
        return

    expected_payload = build_lab_payload(registry)
    actual = _normalise_payload_for_freshness(actual_payload)
    expected = _normalise_payload_for_freshness(expected_payload)
    if actual != expected:
        actual_summary = actual.get("summary", {})
        expected_summary = expected.get("summary", {})
        report.add(
            "error",
            "stale-browser-requirements-registry",
            (
                "Generated browser requirements registry does not match docs-derived "
                f"payload. Browser blocks/apps={actual_summary.get('total_blocks')}/"
                f"{actual_summary.get('app_contracts')} docs blocks/apps="
                f"{expected_summary.get('total_blocks')}/{expected_summary.get('app_contracts')}."
            ),
            file=_relative_path(registry_js, report.repo_root),
        )


def _check_diagnostic_code_shapes(report: SanityReport) -> None:
    scripts_root = report.repo_root / "main_computer/web/applications/scripts"
    diagnostic_files = [
        scripts_root / "mcel-self-diagnosis.js",
        scripts_root / "mcel-diagnostics-counter-widget.js",
    ]
    code_counts: Counter[str] = Counter()

    for path in diagnostic_files:
        if not path.exists():
            report.add(
                "warning",
                "missing-diagnostic-code-source",
                "Expected diagnostic-code source file is missing.",
                file=_relative_path(path, report.repo_root),
            )
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in DIAGNOSTIC_CODE_PATTERN.finditer(text):
            code = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            code_counts[code] += 1
            if not NORMALIZED_CODE_PATTERN.match(code):
                report.add(
                    "error",
                    "unnormalized-diagnostic-code",
                    f"Diagnostic/finding code {code!r} must be lowercase dotted/dashed/underscored text.",
                    file=_relative_path(path, report.repo_root),
                    line=line,
                )

    if not code_counts:
        report.add(
            "warning",
            "no-static-diagnostic-codes-found",
            "No static diagnostic `code: \"...\"` strings were found in MCEL diagnosis scripts.",
        )


def run_sanity_check(repo_root: Path | str | None = None, *, strict: bool = False) -> SanityReport:
    root = Path(repo_root) if repo_root is not None else repo_root_from_here()
    root = root.resolve()
    registry = build_registry(root, strict_schema=strict)

    report = SanityReport(repo_root=root, strict=strict, registry_summary=registry.summary())
    _check_registry_health(report, registry)
    _check_expected_docs(report, registry)
    _check_app_contract_coverage(report, registry)
    _check_form_primitives(report, registry)
    _check_runtime_linkage(report, registry)
    _check_browser_registry_freshness(report, registry)
    _check_diagnostic_code_shapes(report)

    report.issues.sort(
        key=lambda issue: (
            {"error": 0, "warning": 1, "info": 2}.get(issue.severity, 3),
            issue.code,
            issue.file or "",
            issue.line or 0,
            issue.block_id or "",
        )
    )
    return report


def render_text_report(report: SanityReport) -> str:
    data = report.to_dict()
    lines = [
        SANITY_VERSION,
        f"repo: {report.repo_root}",
        f"strict: {str(report.strict).lower()}",
        f"valid: {str(report.valid).lower()}",
        (
            f"errors: {data['counts']['errors']}  "
            f"warnings: {data['counts']['warnings']}  "
            f"infos: {data['counts']['infos']}"
        ),
    ]

    summary = report.registry_summary
    if summary:
        lines.extend(
            [
                (
                    "registry: "
                    f"{summary.get('total_blocks', 0)} blocks, "
                    f"{len(summary.get('app_contracts', []))} app contracts, "
                    f"strict_schema_ready={str(summary.get('strict_schema_ready')).lower()}"
                ),
                f"apps: {', '.join(summary.get('app_contracts', []))}",
            ]
        )

    if report.issues:
        lines.append("issues:")
        for issue in report.issues:
            location = ""
            if issue.file:
                location = issue.file
                if issue.line:
                    location = f"{location}:{issue.line}"
                location = f"{location}: "
            block = f" [{issue.block_id}]" if issue.block_id else ""
            lines.append(f"  {issue.severity}: {location}{issue.code}{block}: {issue.message}")
    else:
        lines.append("issues: none")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static MCEL documentation/spec sanity checks.")
    parser.add_argument("--repo-root", type=Path, default=repo_root_from_here(), help="Repository root")
    parser.add_argument("--strict", action="store_true", help="Use strict schema mode for the underlying registry")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Return a non-zero exit code when warnings are present",
    )
    args = parser.parse_args(argv)

    report = run_sanity_check(args.repo_root, strict=args.strict)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_text_report(report), end="")

    if report.errors:
        return 1
    if args.fail_on_warnings and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
