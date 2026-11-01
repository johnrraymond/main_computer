"""Counter-bounded compiler front end for the official ``mcel.dsl.v1`` syntax.

Wave 2B evaluates one strict CommonJS DSL source through the restricted Node
construction runtime, validates the resulting ``mcel.application-ir.v1`` graph,
optionally compares it to a legacy IR authority, and may stage the normalized
candidate under ``runtime/state/mcel/compiler-candidates``.  It does not project
contracts, mutate a live application, promote a candidate, or reuse evidence.
"""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from main_computer.mcel_application_ir import (
    canonical_json_bytes,
    compare_application_ir,
    validate_application_ir,
)

DSL_COMPILER_REPORT_SCHEMA = "mcel.dsl-compiler-report.v1"
DSL_RUNTIME_RESULT_SCHEMA = "mcel.dsl-runtime-result.v1"
DSL_COMPILER_VERSION = "mcel-dsl-compiler-wave2b"
DSL_LANGUAGE = "mcel.dsl.v1"
DEFAULT_TIMEOUT_MS = 1_000

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NODE_RUNTIME = Path(__file__).resolve().with_name("mcel_dsl_runtime.js")
DEFAULT_CANDIDATE_ROOT = REPOSITORY_ROOT / "runtime" / "state" / "mcel" / "compiler-candidates"


@dataclass(frozen=True)
class DSLCompilerReport:
    valid: bool
    status: str
    app_id: str
    source: str
    diagnostics: tuple[Mapping[str, Any], ...]
    normalized_ir: Mapping[str, Any] | None
    semantic_fingerprint: str | None
    source_binding_fingerprint: str | None
    comparison: Mapping[str, Any] | None
    candidate_directory: Path | None
    candidate_ir_path: Path | None
    candidate_report_path: Path | None
    node_executable: str | None
    node_version: str | None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    @property
    def comparison_status(self) -> str | None:
        if not self.comparison:
            return None
        return str(self.comparison.get("status") or "") or None

    def to_dict(self, *, include_ir: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": DSL_COMPILER_REPORT_SCHEMA,
            "compiler": {"id": "mcel.dsl.compiler", "version": DSL_COMPILER_VERSION},
            "language": DSL_LANGUAGE,
            "valid": self.valid,
            "status": self.status,
            "appId": self.app_id,
            "source": self.source,
            "diagnosticCount": self.diagnostic_count,
            "diagnostics": [dict(item) for item in self.diagnostics],
            "semanticFingerprint": self.semantic_fingerprint,
            "sourceBindingFingerprint": self.source_binding_fingerprint,
            "comparison": dict(self.comparison) if self.comparison else None,
            "candidate": {
                "directory": _display_path(self.candidate_directory),
                "ir": _display_path(self.candidate_ir_path),
                "report": _display_path(self.candidate_report_path),
            }
            if self.candidate_directory
            else None,
            "environment": {
                "nodeExecutable": self.node_executable,
                "nodeVersion": self.node_version,
            },
            "authority": {
                "liveApplicationChanged": False,
                "contractsGenerated": False,
                "candidatePromoted": False,
                "evidenceReused": False,
            },
        }
        if include_ir:
            result["ir"] = self.normalized_ir
        return result


def compile_dsl_application(
    source_path: Path,
    *,
    compare_ir_path: Path | None = None,
    html_path: Path | None = None,
    write_candidate: bool = False,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    node_executable: str | None = None,
) -> DSLCompilerReport:
    source_path = source_path.resolve()
    source_display = _repository_relative(source_path)
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _failure_report(
            source_display,
            "MCEL_DSL_SOURCE_UNREADABLE",
            f"Unable to read DSL source: {exc}",
        )

    # application.html is automatically paired with application.js. Existing
    # JS-only sources remain supported without introducing a second compiler.
    if html_path is None and source_path.name == "application.js":
        companion = source_path.with_name("application.html")
        if companion.exists():
            html_path = companion
    html_text = None
    html_source = None
    if html_path is not None:
        html_path = html_path.resolve()
        html_source = _repository_relative(html_path)
        try:
            html_text = html_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return _failure_report(source_display, "MCEL_HTML_SOURCE_UNREADABLE", f"Cannot read semantic HTML: {exc}")

    node = node_executable or shutil.which("node")
    if not node:
        return _failure_report(
            source_display,
            "MCEL_DSL_NODE_UNAVAILABLE",
            "The Wave 2B compiler requires a Node executable for restricted vanilla-JavaScript construction.",
        )
    node_version = _node_version(node)

    runtime_request = {
        "sourcePath": source_display,
        "sourceText": source_text,
        "timeoutMs": int(timeout_ms),
    }
    try:
        completed = subprocess.run(
            [node, str(NODE_RUNTIME)],
            input=json.dumps(runtime_request, sort_keys=True),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            timeout=max(2.0, timeout_ms / 1_000 + 2.0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _failure_report(
            source_display,
            "MCEL_DSL_EVALUATION_TIMEOUT",
            f"DSL construction exceeded the {timeout_ms} ms compiler limit.",
            node_executable=node,
            node_version=node_version,
        )
    except OSError as exc:
        return _failure_report(
            source_display,
            "MCEL_DSL_NODE_EXECUTION_FAILED",
            f"Unable to execute the restricted Node compiler runtime: {exc}",
            node_executable=node,
            node_version=node_version,
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Node runtime failed without output.").strip()
        return _failure_report(
            source_display,
            "MCEL_DSL_NODE_EXECUTION_FAILED",
            detail,
            node_executable=node,
            node_version=node_version,
        )
    try:
        runtime_result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _failure_report(
            source_display,
            "MCEL_DSL_RUNTIME_PROTOCOL_INVALID",
            f"Restricted Node runtime returned invalid JSON: {exc}",
            node_executable=node,
            node_version=node_version,
        )
    if runtime_result.get("schema") != DSL_RUNTIME_RESULT_SCHEMA:
        return _failure_report(
            source_display,
            "MCEL_DSL_RUNTIME_PROTOCOL_INVALID",
            "Restricted Node runtime returned an unknown result schema.",
            observed=runtime_result.get("schema"),
            node_executable=node,
            node_version=node_version,
        )
    if not runtime_result.get("valid"):
        diagnostics = tuple(_normalize_runtime_diagnostic(item, source_display) for item in runtime_result.get("diagnostics") or ())
        return DSLCompilerReport(
            valid=False,
            status="invalid-dsl",
            app_id="unknown-app",
            source=source_display,
            diagnostics=diagnostics,
            normalized_ir=None,
            semantic_fingerprint=None,
            source_binding_fingerprint=None,
            comparison=None,
            candidate_directory=None,
            candidate_ir_path=None,
            candidate_report_path=None,
            node_executable=node,
            node_version=node_version,
        )

    ir_payload = runtime_result.get("ir")
    if html_text is not None or (isinstance(ir_payload, Mapping) and any(
        isinstance(s, Mapping) and "htmlBindings" in s for s in ir_payload.get("surfaces", [])
    )):
        from main_computer.mcel_semantic_html import SemanticHtmlError, parse_semantic_html
        if html_text is None:
            return _failure_report(source_display, "MCEL_HTML_COMPANION_REQUIRED", "HTML-bound JavaScript requires an application.html companion.")
        try:
            assert isinstance(ir_payload, Mapping)
            regions = parse_semantic_html(html_text, app_id=ir_payload["application"]["appId"], source=html_source)
            _attach_semantic_html(ir_payload, regions, html_source, html_text)
        except SemanticHtmlError as exc:
            diagnostic = _diagnostic(exc.code, str(exc), html_source, semantic_path=f"{html_source}:{exc.line}:{exc.column}")
            return _html_failure(source_display, diagnostic, node, node_version)
        except (ValueError, KeyError, TypeError) as exc:
            diagnostic = _diagnostic("MCEL_HTML_BINDING_INVALID", str(exc), html_source, semantic_path="$html")
            return _html_failure(source_display, diagnostic, node, node_version)

    ir_report = validate_application_ir(ir_payload)
    diagnostics = tuple(item.to_dict() for item in ir_report.diagnostics)
    if not ir_report.valid or ir_report.normalized is None:
        return DSLCompilerReport(
            valid=False,
            status="invalid-ir",
            app_id=ir_report.app_id,
            source=source_display,
            diagnostics=diagnostics,
            normalized_ir=None,
            semantic_fingerprint=None,
            source_binding_fingerprint=None,
            comparison=None,
            candidate_directory=None,
            candidate_ir_path=None,
            candidate_report_path=None,
            node_executable=node,
            node_version=node_version,
        )

    normalized = ir_report.normalized
    comparison: Mapping[str, Any] | None = None
    status = "pass"
    if compare_ir_path is not None:
        try:
            legacy = json.loads(compare_ir_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostic = _diagnostic(
                "MCEL_DSL_COMPARISON_SOURCE_INVALID",
                f"Unable to read comparison IR: {exc}",
                source_display,
                semantic_path="$comparison",
            )
            return DSLCompilerReport(
                valid=False,
                status="comparison-unavailable",
                app_id=ir_report.app_id,
                source=source_display,
                diagnostics=diagnostics + (diagnostic,),
                normalized_ir=normalized,
                semantic_fingerprint=ir_report.semantic_fingerprint,
                source_binding_fingerprint=ir_report.source_binding_fingerprint,
                comparison=None,
                candidate_directory=None,
                candidate_ir_path=None,
                candidate_report_path=None,
                node_executable=node,
                node_version=node_version,
            )
        comparison = compare_application_ir(normalized, legacy)
        if comparison.get("status") != "exact":
            status = "semantic-conflict"
            diagnostics += (
                _diagnostic(
                    "MCEL_DSL_SEMANTIC_EQUIVALENCE_FAILED",
                    "DSL candidate does not have exact semantic equivalence with the comparison IR.",
                    source_display,
                    semantic_path="$comparison",
                    observed={
                        "candidate": ir_report.semantic_fingerprint,
                        "comparison": comparison.get("rightSemanticFingerprint"),
                        "status": comparison.get("status"),
                    },
                    expected={"status": "exact"},
                ),
            )

    candidate_directory: Path | None = None
    candidate_ir_path: Path | None = None
    candidate_report_path: Path | None = None
    if write_candidate:
        source_binding = str(ir_report.source_binding_fingerprint or "")
        digest = source_binding.removeprefix("sha256:")
        candidate_directory = candidate_root.resolve() / ir_report.app_id / digest
        candidate_ir_path = candidate_directory / "mcel.application.ir.json"
        candidate_report_path = candidate_directory / "compiler-report.json"
        candidate_directory.mkdir(parents=True, exist_ok=True)
        candidate_ir_path.write_bytes(canonical_json_bytes(normalized) + b"\n")

    valid = status == "pass" and not any(bool(item.get("blocking", True)) for item in diagnostics)
    report = DSLCompilerReport(
        valid=valid,
        status=status,
        app_id=ir_report.app_id,
        source=source_display,
        diagnostics=diagnostics,
        normalized_ir=normalized,
        semantic_fingerprint=ir_report.semantic_fingerprint,
        source_binding_fingerprint=ir_report.source_binding_fingerprint,
        comparison=comparison,
        candidate_directory=candidate_directory,
        candidate_ir_path=candidate_ir_path,
        candidate_report_path=candidate_report_path,
        node_executable=node,
        node_version=node_version,
    )
    if candidate_report_path is not None:
        candidate_report_path.write_bytes(canonical_json_bytes(report.to_dict(include_ir=False)) + b"\n")
    return report


def _node_version(node: str) -> str | None:
    try:
        completed = subprocess.run([node, "--version"], text=True, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None


def _repository_relative(path: Path) -> str:
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _html_failure(source, diagnostic, node, node_version):
    return DSLCompilerReport(
        valid=False, status="invalid-html", app_id="unknown-app", source=source,
        diagnostics=(diagnostic,), normalized_ir=None, semantic_fingerprint=None,
        source_binding_fingerprint=None, comparison=None, candidate_directory=None,
        candidate_ir_path=None, candidate_report_path=None,
        node_executable=node, node_version=node_version,
    )


def _attach_semantic_html(ir: Mapping, regions: list[dict], source: str, html: str) -> None:
    """Join JS node ownership and HTML hierarchy before canonical IR validation."""
    surfaces = ir.get("surfaces", [])
    if len(surfaces) != 1 or ir.get("layouts"):
        raise ValueError("Paired HTML requires exactly one bound surface and no authored layout tree.")
    surface = surfaces[0]
    if not isinstance(surface, dict) or set(surface) & {'interfaceIntent', 'semanticSurface'}:
        raise ValueError("Paired HTML cannot coexist with JavaScript-authored hierarchy or host-bound markup.")
    bindings = surface.pop("htmlBindings", None)
    if not isinstance(bindings, dict) or set(bindings) != {'schema','regions','relations'} or bindings['schema'] != 'mcel.html-bindings.v1':
        raise ValueError("Use dsl.surface.bindHtml(...) to bind JS surface nodes to application.html.")
    by_id = {region['id']: region for region in regions}
    seen = set()
    for assignment in bindings['regions']:
        rid = assignment.get('id')
        if rid not in by_id or rid in seen:
            raise ValueError(f"Unknown or duplicate authored HTML binding region: {rid!r}.")
        seen.add(rid)
        by_id[rid]['members'] = assignment['members']
    node_ids = {node['id'] for node in surface.get('nodes', [])}
    all_members = [member['ref'] for region in regions for member in region['members']]
    if len(all_members) != len(set(all_members)) or set(all_members) != node_ids:
        raise ValueError("Every JS surface node must bind to exactly one authored HTML region.")
    for relation in bindings['relations']:
        if relation['from']['ref'] not in by_id or relation['to']['ref'] not in by_id:
            raise ValueError("Semantic relationship endpoints must exist in application.html.")
    entities = {item.get('id'):item for section in ('models','states') for item in ir.get(section, [])}
    for region in regions:
        content = region.get('content')
        if content:
            target = entities.get(content['ref'])
            if target is None:
                raise ValueError(f"Unresolved HTML content reference: {content['ref']}.")
            schema = target.get('schema', {})
            data_kind = schema.get('kind') if isinstance(schema,dict) else None
            expected = {'collection':'list', 'scalar':('string','integer','number','boolean','text')}
            accepted = expected.get(content['kind'])
            if accepted and data_kind and data_kind not in (accepted if isinstance(accepted, tuple) else (accepted,)):
                raise ValueError(f"Content kind {content['kind']} is incompatible with {content['ref']} schema kind {data_kind}.")
    roots = [r['id'] for r in regions if r['parentId'] is None]
    surface['interfaceIntent'] = {
        'schema': 'mcel.interface-intent.v1',
        'regions': regions,
        'relations': bindings['relations'],
        'hierarchy': {'rootId': roots[0], 'source': source},
    }
    ir['provenance']['frontend']['sourceFiles'].append({
        'path': source, 'sha256': 'sha256:' + hashlib.sha256(html.encode('utf-8')).hexdigest(),
    })
    for region in regions:
        ir['provenance']['nodeBindings'].append({
            'id': 'binding:' + region['id'],
            'semanticId': region['id'],
            'source': {
                'kind': 'dsl-source-binding', 'frontend':'mcel.semantic-html.v1',
                'file': source,
                'start': {'line':region['htmlSource']['line'], 'column':region['htmlSource']['column']},
                'end': {'line':region['htmlSource']['line'], 'column':region['htmlSource']['column']},
            }
        })


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return _repository_relative(path.resolve())


def _normalize_runtime_diagnostic(item: Any, source: str) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        return _diagnostic("MCEL_DSL_RUNTIME_DIAGNOSTIC_INVALID", "DSL runtime returned an invalid diagnostic.", source)
    result = dict(item)
    result.setdefault("source", {"file": source, "kind": "dsl-source-binding", "frontend": DSL_LANGUAGE})
    result.setdefault("blocking", True)
    result.setdefault("severity", "error")
    result.setdefault("repairStage", "compile")
    return result


def _diagnostic(
    code: str,
    problem: str,
    source: str,
    *,
    semantic_path: str = "$source",
    observed: Any = None,
    expected: Any = None,
) -> Mapping[str, Any]:
    return {
        "code": code,
        "semanticPath": semantic_path,
        "summary": problem,
        "problem": problem,
        "observed": observed,
        "expected": expected,
        "blocking": True,
        "severity": "error",
        "repairStage": "compile",
        "source": {"file": source, "kind": "dsl-source-binding", "frontend": DSL_LANGUAGE},
    }


def _failure_report(
    source: str,
    code: str,
    problem: str,
    *,
    observed: Any = None,
    node_executable: str | None = None,
    node_version: str | None = None,
) -> DSLCompilerReport:
    return DSLCompilerReport(
        valid=False,
        status="environment-failure" if code.startswith("MCEL_DSL_NODE") else "invalid-dsl",
        app_id="unknown-app",
        source=source,
        diagnostics=(_diagnostic(code, problem, source, observed=observed),),
        normalized_ir=None,
        semantic_fingerprint=None,
        source_binding_fingerprint=None,
        comparison=None,
        candidate_directory=None,
        candidate_ir_path=None,
        candidate_report_path=None,
        node_executable=node_executable,
        node_version=node_version,
    )
