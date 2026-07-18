#!/usr/bin/env python3
"""Build a machine-readable registry from MCEL requirements documentation.

The registry is deliberately documentation-first. It parses fenced Markdown blocks named
``mcel-*`` under ``pretty_docs/``, preserves their source locations, validates stable IDs,
and reports schema gaps without pretending that planned prose is implementation proof.

The first registry operates in adoption mode:
- hard errors: malformed/missing IDs and duplicate IDs
- warnings: strict-schema gaps, app-specific risk aliases, and planned/current ambiguity

Use ``--strict-schema`` when the app requirement docs have been normalized enough that
missing required fields and unknown/custom vocabulary should fail CI.
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


REGISTRY_VERSION = "mcel-requirements-registry-v1"

FENCE_PATTERN = re.compile(r"```(mcel-[a-z-]+)\n(.*?)\n```", re.DOTALL)
TOP_LEVEL_FIELD_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$")

STATUS_VALUES = {
    "draft",
    "planned",
    "specified",
    "partially-implemented",
    "implemented",
    "verified",
    "current-plus-planned",
    "open",
    "prohibited",
    "deprecated",
}

RUNTIME_STATUS_VALUES = {
    "structural-only",
    "domain-enrichment-only",
    "scope-limited-semantic-runtime",
    "fullApplicationSemanticReady",
    "not-registered",
}

ADAPTER_STATUS_VALUES = {
    "not-registered",
    "declared-only",
    "preflight-only",
    "executable",
    "prohibited",
}

CANONICAL_RISKS = {
    "read-only",
    "local-state",
    "local-file-mutation",
    "local-repository-mutation",
    "remote-mutation",
    "execution",
    "security-sensitive",
    "prohibited",
}

# The existing app requirement docs predate the strict risk vocabulary. Classify their
# app-specific labels so the registry can be useful before the docs are normalized.
RISK_ALIASES = {
    "low": "read-only",
    "safe-read": "read-only",
    "safe-or-planned-read": "read-only",
    "repository-evidence-read": "read-only",
    "publish-preflight": "read-only",
    "model-provider": "read-only",
    "external-navigation": "read-only",
    "bounded-backend-evaluation": "local-state",
    "local-draft": "local-state",
    "local-draft-change": "local-state",
    "local-state-change": "local-state",
    "local-runtime-mutation": "local-state",
    "local-file-write": "local-file-mutation",
    "local-write": "local-file-mutation",
    "filesystem-mutation": "local-file-mutation",
    "local-file-and-data-mutation": "local-file-mutation",
    "source-metadata-mutation": "local-file-mutation",
    "repository-mutation": "local-repository-mutation",
    "publish-mutation": "remote-mutation",
    "remote-configuration": "remote-mutation",
    "local-or-dev-runtime-mutation": "remote-mutation",
    "cross-app-handoff": "local-state",
    "command-execution": "execution",
    "arbitrary-command-execution": "execution",
    "destructive": "prohibited",
}

DEFAULT_REQUIRED_FIELDS = {
    "mcel-app": [
        "id",
        "title",
        "status",
        "current_runtime_status",
        "target_runtime_status",
        "dominant_object",
        "primary_user_goal",
        "current_sources",
        "verification",
    ],
    "mcel-use-case": ["id", "app", "status", "type", "primary_object", "user_goal", "acceptance"],
    "mcel-object": ["id", "app", "status", "object", "identity", "state_model", "owned_by"],
    "mcel-region": ["id", "app", "status", "region", "role", "responsibility"],
    "mcel-requirement": [
        "id",
        "app",
        "status",
        "type",
        "aspect",
        "object",
        "requirement",
        "acceptance",
    ],
    "mcel-intent": ["id", "app", "status", "intent", "risk", "requires", "produces"],
    "mcel-acceptance": ["id", "app", "status", "requires"],
    "mcel-finding": ["id", "app", "status", "aspect", "severity", "problem", "desired_behavior"],
    "mcel-evidence": ["id", "app", "status", "evidence", "proves", "source", "freshness"],
    "mcel-receipt": ["id", "app", "status", "receipt", "emitted_after", "must_include", "recovery"],
    "mcel-boundary": [
        "id",
        "app",
        "status",
        "boundary",
        "left_side",
        "right_side",
        "rule",
        "prohibited_confusion",
    ],
    "mcel-risk": ["id", "app", "status", "risk", "applies_to", "requires", "must_not_allow"],
    "mcel-adapter": [
        "id",
        "app",
        "status",
        "adapter",
        "current_runtime_status",
        "target_runtime_status",
        "required_intents",
        "readiness_gate",
    ],
    "mcel-layout-pattern": ["id", "status", "pattern", "regions", "responsibility_law", "applies_to"],
    "mcel-source-binding": [
        "id",
        "app",
        "status",
        "target",
        "source_candidates",
        "binding_confidence",
        "verification",
    ],
    "mcel-test-binding": [
        "id",
        "app",
        "status",
        "target",
        "test_candidates",
        "missing_tests",
        "verification",
    ],
    "mcel-runtime-check": [
        "id",
        "app",
        "status",
        "mode",
        "contract",
        "check",
        "severity",
        "observes",
        "expects",
    ],
    "mcel-grammar": ["id", "status", "block", "purpose", "required_fields"],
}

APP_SCOPED_BLOCK_TYPES = {
    block_type
    for block_type in DEFAULT_REQUIRED_FIELDS
    if block_type not in {"mcel-app", "mcel-layout-pattern", "mcel-grammar"}
}


@dataclass(frozen=True)
class RegistryIssue:
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


@dataclass(frozen=True)
class RegistryBlock:
    block_type: str
    block_id: str
    fields: dict[str, Any]
    source_file: str
    start_line: int
    end_line: int
    raw: str
    canonical_risk: str | None = None

    @property
    def app(self) -> str | None:
        app = self.fields.get("app")
        return app if isinstance(app, str) and app else None

    @property
    def status(self) -> str | None:
        status = self.fields.get("status")
        return status if isinstance(status, str) and status else None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.block_type,
            "id": self.block_id,
            "app": self.app,
            "status": self.status,
            "source": {
                "file": self.source_file,
                "start_line": self.start_line,
                "end_line": self.end_line,
            },
            "fields": self.fields,
        }
        if self.canonical_risk:
            data["canonical_risk"] = self.canonical_risk
        return data


@dataclass
class RequirementsRegistry:
    repo_root: Path
    pretty_docs_root: Path
    blocks: list[RegistryBlock] = field(default_factory=list)
    errors: list[RegistryIssue] = field(default_factory=list)
    warnings: list[RegistryIssue] = field(default_factory=list)
    grammar_required_fields: dict[str, list[str]] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def strict_schema_ready(self) -> bool:
        strict_codes = {"missing-required-field", "unknown-status", "unknown-adapter-status", "missing-app-use-case"}
        return not self.errors and not any(issue.code in strict_codes for issue in self.warnings)

    def by_id(self) -> dict[str, RegistryBlock]:
        return {block.block_id: block for block in self.blocks}

    def by_app(self) -> dict[str, list[RegistryBlock]]:
        grouped: dict[str, list[RegistryBlock]] = defaultdict(list)
        for block in self.blocks:
            app = block.app
            if app:
                grouped[app].append(block)
            elif block.block_type == "mcel-app":
                grouped[block.block_id].append(block)
        return dict(sorted(grouped.items()))

    def summary(self) -> dict[str, Any]:
        block_type_counts = Counter(block.block_type for block in self.blocks)
        app_counts = Counter(block.app for block in self.blocks if block.app)
        app_contracts = sorted(block.block_id for block in self.blocks if block.block_type == "mcel-app")
        return {
            "registry_version": REGISTRY_VERSION,
            "repo_root": str(self.repo_root),
            "pretty_docs_root": str(self.pretty_docs_root.relative_to(self.repo_root)),
            "valid": self.valid,
            "strict_schema_ready": self.strict_schema_ready,
            "total_blocks": len(self.blocks),
            "block_type_counts": dict(sorted(block_type_counts.items())),
            "app_counts": dict(sorted(app_counts.items())),
            "app_contracts": app_contracts,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }

    def to_dict(self, *, include_blocks: bool = True) -> dict[str, Any]:
        data = self.summary()
        data["errors"] = [issue.to_dict() for issue in self.errors]
        data["warnings"] = [issue.to_dict() for issue in self.warnings]
        if include_blocks:
            data["blocks"] = [block.to_dict() for block in self.blocks]
        return data


def repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[1]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_block_fields(block_body: str) -> dict[str, Any]:
    """Parse the simple top-level YAML-like fields used by MCEL doc blocks.

    This intentionally supports only the current docs language: top-level scalar keys,
    folded text via ``>``, and simple list fields. It does not execute YAML tags or parse
    arbitrary nested objects.
    """

    lines = block_body.splitlines()
    fields: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        match = TOP_LEVEL_FIELD_PATTERN.match(line)
        if not match:
            i += 1
            continue

        key = match.group(1)
        rest = (match.group(2) or "").strip()
        if rest == ">":
            i += 1
            folded: list[str] = []
            while i < len(lines) and not TOP_LEVEL_FIELD_PATTERN.match(lines[i]):
                value = lines[i].strip()
                if value:
                    folded.append(value)
                i += 1
            fields[key] = " ".join(folded).strip()
            continue

        if rest:
            fields[key] = _strip_quotes(rest)
            i += 1
            continue

        i += 1
        items: list[str] = []
        raw_lines: list[str] = []
        while i < len(lines) and not TOP_LEVEL_FIELD_PATTERN.match(lines[i]):
            raw_line = lines[i]
            raw_lines.append(raw_line)
            item_match = re.match(r"^\s*-\s*(.*)$", raw_line)
            if item_match:
                items.append(_strip_quotes(item_match.group(1).strip()))
            elif raw_line.strip():
                items.append(_strip_quotes(raw_line.strip()))
            i += 1

        if items:
            fields[key] = items
        elif raw_lines:
            fields[key] = "\n".join(raw_lines).strip()
        else:
            fields[key] = ""

    return fields


def discover_markdown_files(pretty_docs_root: Path) -> list[Path]:
    return sorted(
        path
        for path in pretty_docs_root.glob("*.md")
        if path.is_file() and path.name.startswith("mcel-")
    )


def _line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_blocks_from_file(path: Path, repo_root: Path) -> tuple[list[RegistryBlock], list[RegistryIssue]]:
    text = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(repo_root).as_posix()
    blocks: list[RegistryBlock] = []
    errors: list[RegistryIssue] = []

    for match in FENCE_PATTERN.finditer(text):
        block_type = match.group(1)
        raw = match.group(2)
        start_line = _line_number_for_offset(text, match.start())
        end_line = _line_number_for_offset(text, match.end())
        fields = parse_block_fields(raw)
        block_id = fields.get("id")
        if not isinstance(block_id, str) or not block_id.strip():
            errors.append(
                RegistryIssue(
                    severity="error",
                    code="missing-id",
                    message=f"{block_type} block is missing a stable id",
                    file=rel_path,
                    line=start_line,
                    block_type=block_type,
                )
            )
            synthetic_id = f"{rel_path}:{start_line}:{block_type}"
        else:
            synthetic_id = block_id.strip()

        risk_value = fields.get("risk")
        canonical_risk = classify_risk(risk_value) if isinstance(risk_value, str) else None
        blocks.append(
            RegistryBlock(
                block_type=block_type,
                block_id=synthetic_id,
                fields=fields,
                source_file=rel_path,
                start_line=start_line,
                end_line=end_line,
                raw=raw,
                canonical_risk=canonical_risk,
            )
        )

    return blocks, errors


def classify_risk(risk: str | None) -> str | None:
    if not risk:
        return None
    value = risk.strip()
    if value in CANONICAL_RISKS:
        return value
    return RISK_ALIASES.get(value)


def derive_required_fields_from_grammar(blocks: Iterable[RegistryBlock]) -> dict[str, list[str]]:
    required = dict(DEFAULT_REQUIRED_FIELDS)
    for block in blocks:
        if block.block_type != "mcel-grammar":
            continue
        grammar_block_name = block.fields.get("block")
        fields = block.fields.get("required_fields")
        if isinstance(grammar_block_name, str) and isinstance(fields, list):
            required[grammar_block_name] = fields
    required.setdefault("mcel-grammar", DEFAULT_REQUIRED_FIELDS["mcel-grammar"])
    return required


def validate_registry(registry: RequirementsRegistry, *, strict_schema: bool = False) -> None:
    seen: dict[str, RegistryBlock] = {}
    for block in registry.blocks:
        if block.block_id in seen:
            first = seen[block.block_id]
            registry.errors.append(
                RegistryIssue(
                    severity="error",
                    code="duplicate-id",
                    message=(
                        f"Duplicate MCEL requirement id {block.block_id!r}; first seen at "
                        f"{first.source_file}:{first.start_line}"
                    ),
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
            )
        else:
            seen[block.block_id] = block

        required_fields = registry.grammar_required_fields.get(block.block_type)
        if not required_fields:
            registry.warnings.append(
                RegistryIssue(
                    severity="warning",
                    code="unknown-block-type",
                    message=f"No grammar definition for block type {block.block_type}",
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
            )
            required_fields = ["id"]

        for field_name in required_fields:
            if not _has_value(block.fields.get(field_name)):
                issue = RegistryIssue(
                    severity="error" if strict_schema else "warning",
                    code="missing-required-field",
                    message=f"{block.block_type} {block.block_id!r} is missing required field {field_name!r}",
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
                (registry.errors if strict_schema else registry.warnings).append(issue)

        status = block.status
        if status and status not in STATUS_VALUES:
            issue = RegistryIssue(
                severity="error" if strict_schema else "warning",
                code="unknown-status",
                message=f"{block.block_id!r} uses unknown status {status!r}",
                file=block.source_file,
                line=block.start_line,
                block_id=block.block_id,
                block_type=block.block_type,
            )
            (registry.errors if strict_schema else registry.warnings).append(issue)

        if block.block_type in APP_SCOPED_BLOCK_TYPES and not block.app:
            issue = RegistryIssue(
                severity="error" if strict_schema else "warning",
                code="missing-app-scope",
                message=f"{block.block_type} {block.block_id!r} should be scoped to an app",
                file=block.source_file,
                line=block.start_line,
                block_id=block.block_id,
                block_type=block.block_type,
            )
            (registry.errors if strict_schema else registry.warnings).append(issue)

        current_adapter_status = block.fields.get("current_adapter_status")
        target_adapter_status = block.fields.get("target_adapter_status")
        for field_name, field_value in [
            ("current_adapter_status", current_adapter_status),
            ("target_adapter_status", target_adapter_status),
        ]:
            if isinstance(field_value, str) and field_value not in ADAPTER_STATUS_VALUES:
                issue = RegistryIssue(
                    severity="error" if strict_schema else "warning",
                    code="unknown-adapter-status",
                    message=f"{block.block_id!r} uses unknown {field_name} {field_value!r}",
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
                (registry.errors if strict_schema else registry.warnings).append(issue)

        risk_value = block.fields.get("risk")
        if isinstance(risk_value, str) and block.canonical_risk is None:
            issue = RegistryIssue(
                severity="error" if strict_schema else "warning",
                code="unknown-risk",
                message=f"{block.block_id!r} uses unclassified risk {risk_value!r}",
                file=block.source_file,
                line=block.start_line,
                block_id=block.block_id,
                block_type=block.block_type,
            )
            (registry.errors if strict_schema else registry.warnings).append(issue)
        elif isinstance(risk_value, str) and risk_value not in CANONICAL_RISKS:
            registry.warnings.append(
                RegistryIssue(
                    severity="warning",
                    code="custom-risk-alias",
                    message=f"{block.block_id!r} maps app-specific risk {risk_value!r} to {block.canonical_risk!r}",
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
            )

        if status == "current-plus-planned":
            registry.warnings.append(
                RegistryIssue(
                    severity="warning",
                    code="current-plus-planned",
                    message=(
                        f"{block.block_id!r} intentionally mixes current and planned state; "
                        "implementation truth must come from adapters/tests, not prose"
                    ),
                    file=block.source_file,
                    line=block.start_line,
                    block_id=block.block_id,
                    block_type=block.block_type,
                )
            )

    _validate_app_level_coverage(registry, strict_schema=strict_schema)


def _validate_app_level_coverage(registry: RequirementsRegistry, *, strict_schema: bool) -> None:
    blocks_by_app = registry.by_app()
    app_blocks = [block for block in registry.blocks if block.block_type == "mcel-app"]
    for app_block in app_blocks:
        app_id = app_block.block_id
        app_blocks_for_id = blocks_by_app.get(app_id, [])
        type_counts = Counter(block.block_type for block in app_blocks_for_id)

        minimums = {
            "mcel-region": 1,
            "mcel-requirement": 1,
            "mcel-intent": 1,
            "mcel-acceptance": 1,
            "mcel-finding": 1,
        }
        for block_type, minimum in minimums.items():
            if type_counts.get(block_type, 0) < minimum:
                issue = RegistryIssue(
                    severity="error" if strict_schema else "warning",
                    code="missing-app-contract-family",
                    message=f"{app_id!r} has no {block_type} blocks in the requirements registry",
                    file=app_block.source_file,
                    line=app_block.start_line,
                    block_id=app_block.block_id,
                    block_type=app_block.block_type,
                )
                (registry.errors if strict_schema else registry.warnings).append(issue)

        if type_counts.get("mcel-use-case", 0) < 1:
            issue = RegistryIssue(
                severity="error" if strict_schema else "warning",
                code="missing-app-use-case",
                message=(
                    f"{app_id!r} has no roadmap mcel-use-case blocks; add one before using the "
                    "doc as a complete app requirements contract"
                ),
                file=app_block.source_file,
                line=app_block.start_line,
                block_id=app_block.block_id,
                block_type=app_block.block_type,
            )
            (registry.errors if strict_schema else registry.warnings).append(issue)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def build_registry(
    repo_root: Path | str | None = None,
    pretty_docs_root: Path | str | None = None,
    *,
    strict_schema: bool = False,
) -> RequirementsRegistry:
    root = Path(repo_root) if repo_root is not None else repo_root_from_here()
    root = root.resolve()
    docs_root = Path(pretty_docs_root) if pretty_docs_root is not None else root / "pretty_docs"
    docs_root = docs_root.resolve()

    registry = RequirementsRegistry(repo_root=root, pretty_docs_root=docs_root)
    for path in discover_markdown_files(docs_root):
        blocks, errors = extract_blocks_from_file(path, root)
        registry.blocks.extend(blocks)
        registry.errors.extend(errors)

    registry.grammar_required_fields = derive_required_fields_from_grammar(registry.blocks)
    validate_registry(registry, strict_schema=strict_schema)
    return registry



def _block_title(block: RegistryBlock) -> str:
    for key in ("title", "name", "intent", "region", "requirement", "user_goal", "problem"):
        value = block.fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return block.block_id


def _status_group_counts(blocks: Iterable[RegistryBlock]) -> dict[str, int]:
    counts = Counter(block.status or "unspecified" for block in blocks)
    return dict(sorted(counts.items()))


def _risk_group_counts(blocks: Iterable[RegistryBlock]) -> dict[str, int]:
    counts = Counter(block.canonical_risk or block.fields.get("risk") or "unspecified" for block in blocks)
    return dict(sorted(counts.items()))


def _adapter_status_counts(blocks: Iterable[RegistryBlock]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for block in blocks:
        for field_name in ("current_adapter_status", "target_adapter_status"):
            value = block.fields.get(field_name)
            if isinstance(value, str) and value.strip():
                counts[f"{field_name}:{value.strip()}"] += 1
    return dict(sorted(counts.items()))



def _list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _field_string(block: RegistryBlock, field_name: str, default: str = "") -> str:
    value = block.fields.get(field_name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _parse_contract_entry(value: str) -> dict[str, str]:
    """Parse compact `id | selector | label` runtime-check list entries."""

    parts = [part.strip() for part in str(value).split("|")]
    if len(parts) >= 3:
        return {"id": parts[0], "selector": parts[1], "label": " | ".join(parts[2:])}
    if len(parts) == 2:
        return {"id": parts[0], "selector": parts[1], "label": parts[0]}
    if parts and parts[0]:
        return {"id": parts[0], "selector": parts[0], "label": parts[0]}
    return {"id": "", "selector": "", "label": ""}


def _runtime_check_to_dict(block: RegistryBlock) -> dict[str, Any]:
    return {
        "id": block.block_id,
        "app": block.app,
        "status": block.status,
        "mode": _field_string(block, "mode"),
        "contract": _field_string(block, "contract"),
        "check": _field_string(block, "check"),
        "severity": _field_string(block, "severity", "warning"),
        "observes": _list_values(block.fields.get("observes")),
        "expects": _list_values(block.fields.get("expects")),
        "forbids": _list_values(block.fields.get("forbids")),
        "primary_surface_id": _field_string(block, "primary_surface_id"),
        "host_selector": _field_string(block, "host_selector"),
        "editor_selector": _field_string(block, "editor_selector"),
        "min_width": _field_string(block, "min_width"),
        "min_height": _field_string(block, "min_height"),
        "required_regions": [_parse_contract_entry(item) for item in _list_values(block.fields.get("required_regions"))],
        "forbidden_regions": [_parse_contract_entry(item) for item in _list_values(block.fields.get("forbidden_regions"))],
        "lifecycle_assertions": _list_values(block.fields.get("lifecycle_assertions")),
        "failure_message": _field_string(block, "failure_message"),
        "next_probe": _field_string(block, "next_probe"),
        "source_binding": _field_string(block, "source_binding"),
        "test_binding": _field_string(block, "test_binding"),
        "source": {
            "file": block.source_file,
            "start_line": block.start_line,
            "end_line": block.end_line,
        },
    }


def _int_field(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_runtime_diagnostic_contracts(registry: RequirementsRegistry) -> dict[str, dict[str, Any]]:
    """Compile `mcel-runtime-check` blocks into browser-consumable diagnosis contracts."""

    runtime_checks = [
        block for block in registry.blocks if block.block_type == "mcel-runtime-check" and block.app
    ]
    grouped: dict[tuple[str, str, str], list[RegistryBlock]] = defaultdict(list)
    for block in runtime_checks:
        app = block.app or ""
        mode = _field_string(block, "mode", "default")
        contract_id = _field_string(block, "contract", f"{app}.contract.{mode}")
        grouped[(app, mode, contract_id)].append(block)

    apps: dict[str, dict[str, Any]] = {}
    for (app, mode, contract_id), blocks in sorted(grouped.items()):
        checks = [_runtime_check_to_dict(block) for block in sorted(blocks, key=lambda item: item.block_id)]
        primary = next((check for check in checks if check["check"] == "primary-surface"), None)
        required_regions: list[dict[str, str]] = []
        forbidden_regions: list[dict[str, str]] = []
        lifecycle_assertions: list[str] = []

        for check in checks:
            required_regions.extend(check["required_regions"])
            forbidden_regions.extend(check["forbidden_regions"])
            if check["check"] == "forbidden-surfaces-hidden" and not check["forbidden_regions"]:
                forbidden_regions.extend(_parse_contract_entry(item) for item in check["forbids"])
            lifecycle_assertions.extend(check["lifecycle_assertions"])

        # Preserve order while de-duplicating compact region entries.
        def unique_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, str]] = []
            for entry in entries:
                key = (entry.get("id", ""), entry.get("selector", ""))
                if not key[0] and not key[1]:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                unique.append(entry)
            return unique

        def unique_strings(values: list[str]) -> list[str]:
            seen: set[str] = set()
            result: list[str] = []
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                result.append(value)
            return result

        primary_surface = {
            "id": primary["primary_surface_id"] if primary else "",
            "label": primary["failure_message"] if primary else "",
            "hostSelector": primary["host_selector"] if primary else "",
            "editorSelector": primary["editor_selector"] if primary else "",
            "minWidth": _int_field(primary["min_width"], 800) if primary else 800,
            "minHeight": _int_field(primary["min_height"], 600) if primary else 600,
        }

        contract = {
            "contractId": contract_id,
            "appId": app,
            "mode": mode,
            "source": "mcel-runtime-check",
            "derivedFromBlockTypes": ["mcel-runtime-check"],
            "primarySurface": primary_surface,
            "requiredRegions": unique_entries(required_regions),
            "forbiddenRegions": unique_entries(forbidden_regions),
            "lifecycleAssertions": unique_strings(lifecycle_assertions),
            "checks": checks,
        }
        apps.setdefault(app, {"app": app, "mode_contracts": {}})
        apps[app]["mode_contracts"][mode] = contract

    return apps


def build_app_contract_summaries(registry: RequirementsRegistry) -> list[dict[str, Any]]:
    """Build compact app summaries for reports and MCEL Lab comparison.

    The payload intentionally stays at the requirements layer. Runtime truth still comes
    from semantic adapters and tests; the browser-side MCEL requirements registry can
    compare this contract with live adapter readiness when those adapters are loaded.
    """

    blocks_by_app = registry.by_app()
    summaries: list[dict[str, Any]] = []
    required_families = {
        "mcel-use-case",
        "mcel-region",
        "mcel-requirement",
        "mcel-intent",
        "mcel-acceptance",
        "mcel-finding",
    }
    for app_block in sorted(
        (block for block in registry.blocks if block.block_type == "mcel-app"),
        key=lambda block: block.block_id,
    ):
        app_id = app_block.block_id
        app_blocks = blocks_by_app.get(app_id, [])
        app_blocks_without_header = [block for block in app_blocks if block is not app_block]
        type_counts = Counter(block.block_type for block in app_blocks)
        intent_blocks = [block for block in app_blocks if block.block_type == "mcel-intent"]
        use_case_blocks = [block for block in app_blocks if block.block_type == "mcel-use-case"]
        region_blocks = [block for block in app_blocks if block.block_type == "mcel-region"]
        finding_blocks = [block for block in app_blocks if block.block_type == "mcel-finding"]
        requirement_blocks = [block for block in app_blocks if block.block_type == "mcel-requirement"]
        runtime_check_blocks = [block for block in app_blocks if block.block_type == "mcel-runtime-check"]

        present_families = {block_type for block_type, count in type_counts.items() if count > 0}
        missing_families = sorted(required_families - present_families)
        status_counts = _status_group_counts(app_blocks_without_header)
        planned_or_open = [
            block.block_id
            for block in app_blocks_without_header
            if block.status in {"draft", "planned", "specified", "open"}
        ]
        mutation_intents = [
            block.block_id
            for block in intent_blocks
            if (block.canonical_risk or "") in {
                "local-state",
                "local-file-mutation",
                "local-repository-mutation",
                "remote-mutation",
                "execution",
                "security-sensitive",
            }
        ]
        prohibited_intents = [
            block.block_id
            for block in intent_blocks
            if (block.canonical_risk or "") == "prohibited" or block.status == "prohibited"
        ]

        summaries.append(
            {
                "app": app_id,
                "id": app_id,
                "title": app_block.fields.get("title", app_id),
                "status": app_block.status,
                "current_runtime_status": app_block.fields.get("current_runtime_status", ""),
                "target_runtime_status": app_block.fields.get("target_runtime_status", ""),
                "dominant_object": app_block.fields.get("dominant_object", ""),
                "primary_user_goal": app_block.fields.get("primary_user_goal", ""),
                "source": {
                    "file": app_block.source_file,
                    "start_line": app_block.start_line,
                    "end_line": app_block.end_line,
                },
                "contract_complete": not missing_families,
                "missing_contract_families": missing_families,
                "block_type_counts": dict(sorted(type_counts.items())),
                "status_counts": status_counts,
                "intent_risk_counts": _risk_group_counts(intent_blocks),
                "adapter_status_counts": _adapter_status_counts(intent_blocks),
                "use_cases": [
                    {
                        "id": block.block_id,
                        "status": block.status,
                        "goal": block.fields.get("user_goal", _block_title(block)),
                    }
                    for block in use_case_blocks
                ],
                "regions": [
                    {
                        "id": block.block_id,
                        "status": block.status,
                        "region": block.fields.get("region", ""),
                        "role": block.fields.get("role", ""),
                        "responsibility": block.fields.get("responsibility", ""),
                    }
                    for block in region_blocks
                ],
                "requirements": [
                    {
                        "id": block.block_id,
                        "status": block.status,
                        "aspect": block.fields.get("aspect", ""),
                        "object": block.fields.get("object", ""),
                    }
                    for block in requirement_blocks
                ],
                "runtime_checks": [
                    {
                        "id": block.block_id,
                        "status": block.status,
                        "mode": block.fields.get("mode", ""),
                        "contract": block.fields.get("contract", ""),
                        "check": block.fields.get("check", ""),
                        "severity": block.fields.get("severity", ""),
                    }
                    for block in runtime_check_blocks
                ],
                "mutation_intents": mutation_intents,
                "prohibited_intents": prohibited_intents,
                "open_finding_ids": [block.block_id for block in finding_blocks if block.status == "open"],
                "planned_or_open_ids": planned_or_open,
                "comparison_seed": {
                    "source": "mcel-requirements-registry",
                    "truth_gate": "implementation truth must come from adapters/tests, not prose",
                    "runtime_comparison_status": "pending-live-adapter-snapshot",
                },
            }
        )

    return summaries


def build_lab_payload(registry: RequirementsRegistry) -> dict[str, Any]:
    app_contracts = build_app_contract_summaries(registry)
    runtime_diagnostic_contracts = build_runtime_diagnostic_contracts(registry)

    def compact_contract(contract: dict[str, Any]) -> dict[str, Any]:
        return {
            "app": contract["app"],
            "id": contract["id"],
            "title": contract["title"],
            "status": contract["status"],
            "current_runtime_status": contract["current_runtime_status"],
            "target_runtime_status": contract["target_runtime_status"],
            "dominant_object": contract["dominant_object"],
            "primary_user_goal": contract["primary_user_goal"],
            "contract_complete": contract["contract_complete"],
            "block_type_counts": contract["block_type_counts"],
            "status_counts": contract["status_counts"],
            "intent_risk_counts": contract["intent_risk_counts"],
            "adapter_status_counts": contract["adapter_status_counts"],
            "use_cases": contract["use_cases"],
            "region_count": len(contract["regions"]),
            "intent_count": contract["block_type_counts"].get("mcel-intent", 0),
            "mutation_intent_count": len(contract["mutation_intents"]),
            "prohibited_intent_count": len(contract["prohibited_intents"]),
            "open_finding_count": len(contract["open_finding_ids"]),
            "planned_or_open_count": len(contract["planned_or_open_ids"]),
            "runtime_check_count": len(contract.get("runtime_checks", [])),
            "runtime_checks": contract.get("runtime_checks", [])[:10],
            "first_regions": contract["regions"][:5],
            "source": contract["source"],
        }

    compact_contracts = [compact_contract(contract) for contract in app_contracts]
    compact_map = {contract["app"]: contract for contract in compact_contracts}
    return {
        "payload_version": "mcel-requirements-lab-payload-v1",
        "registry_version": REGISTRY_VERSION,
        "strict_schema_ready": registry.strict_schema_ready,
        "valid": registry.valid,
        "source": "pretty_docs/*.md",
        "truth_gate": "requirements describe the contract; adapters and tests prove implementation",
        "summary": registry.summary(),
        "apps": compact_contracts,
        "app_contracts": compact_map,
        "runtime_diagnostic_contracts": runtime_diagnostic_contracts,
        "app_comparison_seeds": {
            app_id: {
                "app": app_id,
                "requirements_contract_present": True,
                "requirements_contract_complete": contract["contract_complete"],
                "current_runtime_status": contract["current_runtime_status"],
                "target_runtime_status": contract["target_runtime_status"],
                "required_use_case_count": len(contract["use_cases"]),
                "required_region_count": contract["region_count"],
                "required_intent_count": contract["intent_count"],
                "mutation_intent_count": contract["mutation_intent_count"],
                "prohibited_intent_count": contract["prohibited_intent_count"],
                "runtime_comparison_status": "pending-live-adapter-snapshot",
            }
            for app_id, contract in compact_map.items()
        },
    }


def render_markdown_report(registry: RequirementsRegistry) -> str:
    summary = registry.summary()
    contracts = build_app_contract_summaries(registry)
    lines: list[str] = [
        "# MCEL Requirements Registry Report",
        "",
        "Generated by `python tools/mcel_requirements_registry.py --report`.",
        "",
        "## Registry summary",
        "",
        f"- Registry version: `{summary['registry_version']}`",
        f"- Strict schema ready: `{str(summary['strict_schema_ready']).lower()}`",
        f"- Valid: `{str(summary['valid']).lower()}`",
        f"- Blocks: `{summary['total_blocks']}`",
        f"- Apps: {', '.join(f'`{app}`' for app in summary['app_contracts'])}",
        f"- Errors: `{summary['error_count']}`",
        f"- Warnings: `{summary['warning_count']}`",
        "",
        "## Block coverage",
        "",
    ]
    for block_type, count in summary["block_type_counts"].items():
        lines.append(f"- `{block_type}`: `{count}`")

    lines.extend(["", "## App contracts", ""])
    for contract in contracts:
        lines.extend(
            [
                f"### {contract['title']} (`{contract['app']}`)",
                "",
                f"- Dominant object: {contract['dominant_object']}",
                f"- Primary user goal: {contract['primary_user_goal']}",
                f"- Current runtime status: `{contract['current_runtime_status']}`",
                f"- Target runtime status: `{contract['target_runtime_status']}`",
                f"- Contract complete: `{str(contract['contract_complete']).lower()}`",
                f"- Use cases: `{len(contract['use_cases'])}`",
                f"- Regions: `{len(contract['regions'])}`",
                f"- Intents: `{contract['block_type_counts'].get('mcel-intent', 0)}`",
                f"- Mutation intents: `{len(contract['mutation_intents'])}`",
                f"- Prohibited intents: `{len(contract['prohibited_intents'])}`",
                "",
                "Use-case roadmap:",
            ]
        )
        if contract["use_cases"]:
            for use_case in contract["use_cases"]:
                lines.append(f"- `{use_case['id']}` ({use_case['status']}): {use_case['goal']}")
        else:
            lines.append("- none")
        lines.extend(["", "Intent risk coverage:"])
        if contract["intent_risk_counts"]:
            for risk, count in contract["intent_risk_counts"].items():
                lines.append(f"- `{risk}`: `{count}`")
        else:
            lines.append("- none")
        lines.extend(["", "First layout responsibilities:"])
        for region in contract["regions"][:5]:
            lines.append(
                f"- `{region['id']}` → `{region['region']}`: {region['responsibility']}"
            )
        if len(contract["regions"]) > 5:
            lines.append(f"- ... {len(contract['regions']) - 5} more regions")
        lines.append("")

    lines.extend(
        [
            "## MCEL Lab handoff",
            "",
            "The registry report is requirements evidence, not implementation proof.",
            "MCEL Lab should compare these app contracts against live semantic adapter readiness, rendered layout inspection, and tests before marking requirements verified.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _print_summary(registry: RequirementsRegistry) -> None:
    summary = registry.summary()
    print(f"{summary['registry_version']}")
    print(f"blocks: {summary['total_blocks']}")
    print(f"apps: {', '.join(summary['app_contracts'])}")
    print(f"errors: {summary['error_count']}")
    print(f"warnings: {summary['warning_count']}")
    print(f"strict_schema_ready: {summary['strict_schema_ready']}")
    print("block types:")
    for block_type, count in summary["block_type_counts"].items():
        print(f"  {block_type}: {count}")
    if registry.errors:
        print("errors:")
        for issue in registry.errors:
            print(f"  {issue.file}:{issue.line}: {issue.code}: {issue.message}")
    if registry.warnings:
        print("warnings:")
        for issue in registry.warnings[:20]:
            print(f"  {issue.file}:{issue.line}: {issue.code}: {issue.message}")
        remaining = len(registry.warnings) - 20
        if remaining > 0:
            print(f"  ... {remaining} more warnings")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the MCEL requirements registry from pretty_docs/*.md")
    parser.add_argument("--repo-root", type=Path, default=repo_root_from_here(), help="Repository root")
    parser.add_argument("--pretty-docs", type=Path, default=None, help="Override pretty_docs directory")
    parser.add_argument("--json", action="store_true", help="Emit full registry JSON")
    parser.add_argument(
        "--no-blocks",
        action="store_true",
        help="When used with --json, omit individual block payloads and emit summary/issues only",
    )
    parser.add_argument(
        "--strict-schema",
        action="store_true",
        help="Treat missing required fields and unknown vocabulary as errors",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Emit a human-readable Markdown registry report",
    )
    parser.add_argument(
        "--lab-json",
        action="store_true",
        help="Emit the compact MCEL Lab app-comparison payload",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path for JSON or Markdown output",
    )
    args = parser.parse_args(argv)

    registry = build_registry(
        args.repo_root,
        args.pretty_docs,
        strict_schema=args.strict_schema,
    )

    if args.report:
        data = render_markdown_report(registry)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(data, encoding="utf-8")
        else:
            print(data, end="")
    elif args.lab_json:
        data = json.dumps(build_lab_payload(registry), indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(data + "\n", encoding="utf-8")
        else:
            print(data)
    elif args.json or args.output:
        payload = registry.to_dict(include_blocks=not args.no_blocks)
        data = json.dumps(payload, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(data + "\n", encoding="utf-8")
        else:
            print(data)
    else:
        _print_summary(registry)

    return 0 if registry.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
