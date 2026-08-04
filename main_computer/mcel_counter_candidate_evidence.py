"""Independent evidence pipeline for the isolated DSL-generated Counter candidate.

Wave 5 creates a disposable repository workspace under candidate runtime state,
overlays the generated Counter package, and runs the existing package, runtime,
acceptance, browser-observation, and application-proof authorities there.  It
also produces a Counter-specific effect-accounting ledger from independent Node
and Chromium probes.  The live package remains untouched and no evidence is
reused or promoted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.mcel_node_runtime import resolve_node_executable

REPORT_SCHEMA = "mcel.counter-candidate-evidence-report.v1"
REPORT_VERSION = "mcel-counter-candidate-evidence-wave5"
EFFECT_REPORT_SCHEMA = "mcel.counter-effect-accounting-report.v1"
NODE_PROBE_SCHEMA = "mcel.counter-effect-probe.v1"
BROWSER_PROBE_SCHEMA = "mcel.counter-browser-effect-probe.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")


class CounterCandidateEvidenceError(RuntimeError):
    """Raised when isolated candidate evidence cannot be produced truthfully."""


@dataclass(frozen=True)
class CandidateEvidenceResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(
                self.output_directory, REPOSITORY_ROOT
            )
        return value


def run_counter_candidate_evidence(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
) -> CandidateEvidenceResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / "mcel_apps" / "contract-counter"
    live_before = _tree_fingerprint(live_package)

    dsl = compile_dsl_application(
        dsl_source_path,
        compare_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        write_candidate=True,
    )
    diagnostics.extend(dsl.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not dsl.source_binding_fingerprint:
        return _failure("invalid-dsl", diagnostics, "DSL compilation did not produce valid Counter IR.")

    projection = project_counter_candidate(
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package,
        candidate_root=candidate_root,
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure("invalid-projection", diagnostics, "Candidate projection is not exact.")

    source_binding = dsl.source_binding_fingerprint.removeprefix("sha256:")
    candidate_directory = projection.candidate_directory.resolve()
    workspace = candidate_directory / "evidence-workspace"
    candidate_package = candidate_directory / "package" / "mcel_apps" / "contract-counter"
    candidate_ir_path = candidate_directory / "mcel.application.ir.json"
    if not candidate_ir_path.is_file():
        candidate_ir_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_ir_path.write_bytes(canonical_json_bytes(dsl.normalized_ir) + b"\n")
    if not candidate_package.is_dir():
        return _failure("missing-candidate-package", diagnostics, "Wave 4 candidate package is missing.")

    output_root = report_root if report_root.is_absolute() else repo / report_root
    output_directory = output_root / "contract-counter" / source_binding
    if write_report and output_directory.exists():
        shutil.rmtree(output_directory)

    try:
        _prepare_workspace(repo, workspace, candidate_package)
        _run_candidate_authorities(
            repo=repo,
            workspace=workspace,
            headed=headed,
            command_runner=command_runner or _run_command,
        )
        acceptance = _load_json(
            workspace / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json",
            "candidate acceptance report",
        )
        observation = _load_json(
            workspace / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json",
            "candidate observation report",
        )
        base_proof = _load_json(
            workspace / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json",
            "candidate application proof",
        )
        node_probe = dict((node_probe_runner or _run_counter_effect_probe)(workspace))
        browser_probe = dict((browser_probe_runner or _run_browser_effect_probe)(workspace, headed))
        effect_accounting = _build_effect_accounting(
            ir=dsl.normalized_ir,
            acceptance=acceptance,
            observation=observation,
            node_probe=node_probe,
            browser_probe=browser_probe,
        )
    except CounterCandidateEvidenceError as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_EVIDENCE_FAILED", str(exc), "$candidateEvidence"))
        failure_report: dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "appId": "contract-counter",
            "status": "fail",
            "valid": False,
            "truthStatus": None,
            "candidate": {
                "directory": _display_path(candidate_directory, repo),
                "workspace": _display_path(workspace, repo),
                "semanticFingerprint": dsl.semantic_fingerprint,
                "sourceBindingFingerprint": dsl.source_binding_fingerprint,
                "irSha256": _sha256_path(candidate_ir_path),
            },
            "stages": {
                "candidateProjection": {"status": "pass"},
                "packageValidation": {"status": "not-completed"},
                "runtimeProjection": {"status": "not-completed"},
                "acceptance": {"status": "not-completed"},
                "browserObservation": {"status": "fail" if "observation" in str(exc).lower() or "browser" in str(exc).lower() else "not-completed"},
                "effectAccounting": {"status": "not-completed"},
                "applicationProof": {"status": "not-completed"},
                "repositoryBinding": {"status": "not-completed"},
            },
            "authority": {
                "liveAuthority": "legacy-explicit-package",
                "candidateAuthority": "none",
                "liveApplicationChanged": False,
                "contractsGeneratedInCandidate": True,
                "evidenceReused": False,
                "candidatePromoted": False,
                "promotionEligible": False,
            },
            "error": str(exc),
        }
        if write_report:
            output_directory.mkdir(parents=True, exist_ok=True)
            _write_json(output_directory / "mcel-candidate-evidence-report.json", failure_report)
            (output_directory / "mcel-candidate-evidence-report.md").write_text(
                _render_markdown(failure_report), encoding="utf-8"
            )
        return _result(False, "fail", diagnostics, failure_report, output_directory if write_report else None)

    workspace_catalog = build_application_package_catalog(workspace)
    candidate_record = next(
        (record for record in workspace_catalog.packages if record.app_id == "contract-counter"),
        None,
    )
    workspace_provenance = build_repository_provenance(workspace)
    projection_report = _load_json(candidate_directory / "projection-report.json", "candidate projection report")

    stage_checks = {
        "candidateProjection": projection_report.get("status") == "exact",
        "packageValidation": bool(candidate_record and candidate_record.valid and candidate_record.fingerprint),
        "runtimeProjection": (base_proof.get("stages") or {}).get("generatedArtifacts", {}).get("status") == "pass",
        "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
        "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
        "effectAccounting": effect_accounting.get("status") == "closed" and effect_accounting.get("valid") is True,
        "applicationProof": base_proof.get("status") == "pass" and base_proof.get("truthStatus") == "semantic-runtime-proven",
        "repositoryBinding": (base_proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(
                _diagnostic(
                    "MCEL_COUNTER_CANDIDATE_STAGE_FAILED",
                    f"Candidate stage {stage} did not pass.",
                    f"$stages.{stage}",
                )
            )

    live_after = _tree_fingerprint(live_package)
    live_unchanged = live_before == live_after
    if not live_unchanged:
        diagnostics.append(
            _diagnostic(
                "MCEL_COUNTER_LIVE_PACKAGE_CHANGED",
                "The live Counter package changed during isolated candidate evidence execution.",
                "$authority.liveApplicationChanged",
            )
        )

    valid = all(stage_checks.values()) and live_unchanged and not any(
        item.get("blocking", True) for item in diagnostics
    )
    status = "pass" if valid else "fail"
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": "contract-counter",
        "status": status,
        "valid": valid,
        "truthStatus": base_proof.get("truthStatus"),
        "candidate": {
            "directory": _display_path(candidate_directory, repo),
            "workspace": _display_path(workspace, repo),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "irSha256": _sha256_path(candidate_ir_path),
            "packageFingerprint": candidate_record.fingerprint if candidate_record else None,
            "catalogFingerprint": workspace_catalog.fingerprint,
            "repositoryProvenance": workspace_provenance,
        },
        "stages": {
            stage: {"status": "pass" if passed else "fail"}
            for stage, passed in stage_checks.items()
        },
        "authority": {
            "liveAuthority": "legacy-explicit-package",
            "candidateAuthority": "none",
            "liveApplicationChanged": not live_unchanged,
            "contractsGeneratedInCandidate": True,
            "evidenceReused": False,
            "candidatePromoted": False,
            "promotionEligible": False,
        },
        "effectAccounting": effect_accounting,
        "baseApplicationProof": base_proof,
        "evidence": {},
    }

    if write_report:
        _publish_evidence(
            workspace=workspace,
            output_directory=output_directory,
            report=report,
            effect_accounting=effect_accounting,
            node_probe=node_probe,
            browser_probe=browser_probe,
        )
        report["evidence"] = _published_artifacts(output_directory, repo)
        _write_json(output_directory / "mcel-candidate-evidence-report.json", report)
        (output_directory / "mcel-candidate-evidence-report.md").write_text(
            _render_markdown(report), encoding="utf-8"
        )

    return _result(valid, status, diagnostics, report, output_directory if write_report else None)


def _prepare_workspace(repo: Path, workspace: Path, candidate_package: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    source_dirs = (
        "main_computer",
        "tools",
        "mcel_apps",
        "tests",
        "pretty_docs",
        "contracts",
        "deploy",
        "docker",
        "scripts",
        "game_projects",
    )
    for name in source_dirs:
        source = repo / name
        if source.is_dir():
            shutil.copytree(source, workspace / name, ignore=_copy_ignore)
    for path in repo.iterdir():
        if not path.is_file() or path.suffix.lower() in {".zip", ".pyc", ".pyo"}:
            continue
        shutil.copy2(path, workspace / path.name)
    target = workspace / "mcel_apps" / "contract-counter"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(candidate_package, target, ignore=_copy_ignore)


def _copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git"}
        or name.endswith((".pyc", ".pyo", ".zip"))
    }
    return ignored


def _run_candidate_authorities(
    *,
    repo: Path,
    workspace: Path,
    headed: bool,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    commands = [
        [sys.executable, str(repo / "tools/mcel_application_runtime_projection.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "tools/mcel_application_package_browser_catalog.py"), "--repo-root", str(workspace)],
        [
            sys.executable,
            str(repo / "main_computer/mcel_acceptance_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            "contract-counter",
            "--check",
        ],
        [
            sys.executable,
            str(repo / "main_computer/mcel_application_observation_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            "contract-counter",
            "--check",
            *( ["--headed"] if headed else [] ),
        ],
        [
            sys.executable,
            str(repo / "main_computer/mcel_app_prove.py"),
            "--repo-root",
            str(workspace),
            "--app",
            "contract-counter",
            "--reuse-evidence",
            "--check",
        ],
    ]
    for command in commands:
        completed = command_runner(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            raise CounterCandidateEvidenceError(
                "Candidate authority failed: "
                + " ".join(command)
                + (f"\n{completed.stdout.strip()}" if completed.stdout else "")
            )


def _run_command(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def _run_counter_effect_probe(workspace: Path) -> Mapping[str, Any]:
    node = resolve_node_executable()
    if not node:
        raise CounterCandidateEvidenceError("Node.js is required for Counter effect accounting.")
    scm = (workspace / "main_computer/web/applications/scripts/mcel-scm.js").read_text(encoding="utf-8")
    runtime = (workspace / "main_computer/web/applications/scripts/mcel-application-runtime.js").read_text(encoding="utf-8")
    package = workspace / "mcel_apps/contract-counter"
    script = f'''\
"use strict";
const fs = require("fs");
{scm}
{runtime}
async function importContract(filePath) {{
  const source = fs.readFileSync(filePath, "utf8");
  const url = `data:text/javascript;base64,${{Buffer.from(source, "utf8").toString("base64")}}`;
  return import(url);
}}
(async () => {{
  const domain = await importContract({json.dumps(str(package / "contracts/domain.js"))});
  const intents = await importContract({json.dumps(str(package / "contracts/intents.js"))});
  const adapter = await importContract({json.dumps(str(package / "contracts/adapter.js"))});
  const definition = McelApplicationRuntime.defineApplication({{
    appId: "contract-counter",
    domain: domain.ContractCounterDomain,
    intents: intents.ContractCounterIntents,
    adapter: adapter.ContractCounterAdapter
  }});
  const app = McelApplicationRuntime.createApplicationInstance(definition, {{id: "candidate-effect-probe"}});
  function dispatch(operationId, intentId, expectedRevision, payload = {{}}) {{
    const before = app.readState();
    const result = app.dispatch({{operationId, intentId, expectedRevision, payload}});
    const after = app.readState();
    return {{operationId, intentId, expectedRevision, before, result, after}};
  }}
  const operations = [
    dispatch("candidate-increment", "increment", 0),
    dispatch("candidate-stale", "increment", 0),
    dispatch("candidate-direct-set", "direct-set", 1, {{value: 99}}),
    dispatch("candidate-reset", "reset", 1)
  ];
  process.stdout.write(JSON.stringify({{
    schema: {json.dumps(NODE_PROBE_SCHEMA)},
    appId: "contract-counter",
    initialState: {{count: 0, revision: 0}},
    operations,
    finalState: app.readState(),
    finalRevision: app.revision,
    appliedOperationIds: app.appliedOperationIds
  }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
'''
    script_path = workspace / "runtime/state/mcel/counter-candidate-effect-probe.js"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [str(node), str(script_path)],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise CounterCandidateEvidenceError(
            "Counter effect probe failed" + (f": {completed.stderr.strip()}" if completed.stderr else ".")
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CounterCandidateEvidenceError("Counter effect probe returned invalid JSON.") from exc
    if not isinstance(value, Mapping) or value.get("schema") != NODE_PROBE_SCHEMA:
        raise CounterCandidateEvidenceError("Counter effect probe returned the wrong schema.")
    return value


def _run_browser_effect_probe(workspace: Path, headed: bool) -> Mapping[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CounterCandidateEvidenceError(
            "Playwright is required for isolated candidate browser effect evidence."
        ) from exc
    from main_computer.mcel_application_observation_runner import _StaticServer

    web_root = workspace / "main_computer" / "web"
    with _StaticServer(web_root) as server, sync_playwright() as playwright:
        launch_attempts: list[dict[str, Any]] = [{"headless": not headed}]
        executable = (
            os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
            or os.environ.get("CHROMIUM_EXECUTABLE")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
            or shutil.which("msedge")
        )
        if executable:
            launch_attempts.append(
                {
                    "headless": not headed,
                    "executable_path": executable,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--no-proxy-server",
                        "--proxy-bypass-list=*",
                    ],
                }
            )
        browser = None
        errors: list[str] = []
        for options in launch_attempts:
            try:
                browser = playwright.chromium.launch(**options)
                break
            except PlaywrightError as exc:
                errors.append(str(exc))
        if browser is None:
            raise CounterCandidateEvidenceError(
                "Playwright Chromium is unavailable. " + " | ".join(errors[-2:])
            )
        try:
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                url = f"{server.base_url}/mcel-package-host.html?app=contract-counter&observation=1"
                page.goto(url, wait_until="networkidle")
                page.wait_for_function(
                    "window.McelApplicationPackageHost && "
                    "(window.McelApplicationPackageHost.ready === true || window.McelApplicationPackageHost.error)",
                    timeout=30_000,
                )
                result = _evaluate_browser_effect_probe(page, url)
            except PlaywrightError as exc:
                raise CounterCandidateEvidenceError(
                    f"Candidate browser effect probe failed: {exc}"
                ) from exc
            result["browser"] = {
                "engine": "playwright-chromium",
                "version": browser.version,
                "headless": not headed,
            }
            return result
        finally:
            browser.close()


def _evaluate_browser_effect_probe(page: Any, url: str) -> dict[str, Any]:
    """Run the browser effect probe with all host values passed explicitly."""

    return page.evaluate(
        """async ({pageUrl}) => {
          const host = window.McelApplicationPackageHost;
          if (host.ready !== true) throw new Error(host.error || "candidate host failed");
          const valueNode = () => host.mount.root.querySelector('[data-mcel-node-id="contract-counter.value"]');
          const visible = () => String(valueNode()?.textContent || "").trim();
          const state = () => host.mount.readState();
          const operations = [];
          async function committed(operationId, intentId, expectedRevision) {
            const before = state();
            const outcome = await host.dispatchAndObserve(intentId, {}, {
              operationId,
              expectedRevision,
              repositoryFingerprint: "candidate-browser-effect-probe",
              capturedAt: new Date(0).toISOString(),
              browser: {engine: "playwright-chromium", version: "candidate-effect-probe"},
              viewport: {width: window.innerWidth, height: window.innerHeight, deviceScaleFactor: window.devicePixelRatio}
            });
            operations.push({operationId, intentId, before, result: outcome.operationResult, after: state(), visible: visible(), observation: outcome.observation});
          }
          function refused(operationId, intentId, expectedRevision, payload = {}) {
            const before = state();
            const outcome = host.dispatch(intentId, payload, {operationId, expectedRevision});
            operations.push({operationId, intentId, before, result: outcome, after: state(), visible: visible()});
          }
          const initial = {state: state(), visible: visible()};
          await committed("candidate-browser-increment", "increment", 0);
          refused("candidate-browser-stale", "increment", 0);
          refused("candidate-browser-direct-set", "direct-set", 1, {value: 99});
          await committed("candidate-browser-reset", "reset", 1);
          return {schema: "mcel.counter-browser-effect-probe.v1", appId: "contract-counter", url: pageUrl, initial, operations, final: {state: state(), visible: visible()}};
        }""",
        {"pageUrl": url},
    )


def _build_effect_accounting(
    *,
    ir: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    node_probe: Mapping[str, Any],
    browser_probe: Mapping[str, Any],
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    declared = {
        str(item.get("id")): item
        for item in ir.get("effects") or []
        if isinstance(item, Mapping) and item.get("id")
    }
    expected_ids = {
        "effect:increment.count-write",
        "effect:increment.revision-write",
        "effect:reset.count-write",
        "effect:reset.revision-write",
    }
    if set(declared) != expected_ids:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_DECLARATIONS_INCOMPLETE", "Counter must declare exactly four canonical-write effects.", "$effects"))

    node_ops = {str(item.get("operationId")): item for item in node_probe.get("operations") or []}
    browser_ops = {str(item.get("operationId")): item for item in browser_probe.get("operations") or []}
    required_node = {"candidate-increment", "candidate-stale", "candidate-direct-set", "candidate-reset"}
    required_browser = {"candidate-browser-increment", "candidate-browser-stale", "candidate-browser-direct-set", "candidate-browser-reset"}
    if set(node_ops) != required_node:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_NODE_PROBE_INCOMPLETE", "Node effect probe did not produce all required operations.", "$nodeProbe.operations"))
    if set(browser_ops) != required_browser:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_PROBE_INCOMPLETE", "Browser effect probe did not produce all required operations.", "$browserProbe.operations"))

    instances: list[dict[str, Any]] = []
    operation_specs = [
        ("candidate-increment", "candidate-browser-increment", "increment", "completed"),
        ("candidate-stale", "candidate-browser-stale", "increment", "refused-before-attempt"),
        ("candidate-reset", "candidate-browser-reset", "reset", "completed"),
    ]
    for node_id, browser_id, owner_suffix, disposition in operation_specs:
        node = node_ops.get(node_id) or {}
        browser = browser_ops.get(browser_id) or {}
        before = node.get("before") or {}
        after = node.get("after") or {}
        result = node.get("result") or {}
        browser_before = browser.get("before") or {}
        browser_after = browser.get("after") or {}
        browser_result = browser.get("result") or {}
        if disposition == "completed":
            if result.get("ok") is not True or result.get("status") != "committed":
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_RECEIPT_MISMATCH", f"{node_id} did not commit.", f"$nodeProbe.{node_id}"))
            if browser_result.get("ok") is not True or browser_result.get("status") != "committed":
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_RECEIPT_MISMATCH", f"{browser_id} did not commit.", f"$browserProbe.{browser_id}"))
            observed = browser.get("observation") or {}
            comparison = observed.get("comparison") or {}
            if observed.get("status") != "pass" or comparison.get("surfaceMatches") is not True:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_VISIBLE_OUTCOME_MISMATCH", f"{browser_id} did not produce a passing visible outcome.", f"$browserProbe.{browser_id}.observation"))
            expected_count = 1 if owner_suffix == "increment" else 0
            if browser_after.get("count") != expected_count or str(browser.get("visible")) != str(expected_count):
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_VISIBLE_VALUE_MISMATCH", f"{browser_id} visible count did not match canonical count.", f"$browserProbe.{browser_id}.visible"))
        else:
            expected_code = "SCM_STALE_REVISION"
            if result.get("ok") is not False or result.get("code") != expected_code or before != after:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSAL_MUTATED_STATE", f"{node_id} was not a clean stale-revision refusal.", f"$nodeProbe.{node_id}"))
            if browser_result.get("ok") is not False or browser_result.get("code") != expected_code or browser_before != browser_after:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_REFUSAL_MUTATED_STATE", f"{browser_id} was not a clean browser stale-revision refusal.", f"$browserProbe.{browser_id}"))
            if str(browser.get("visible")) != str(browser_after.get("count")):
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSAL_VISIBLE_DRIFT", f"{browser_id} changed visible state during refusal.", f"$browserProbe.{browser_id}.visible"))

        for state_name in ("count", "revision"):
            effect_id = f"effect:{owner_suffix}.{state_name}-write"
            if effect_id not in declared:
                continue
            actual_changed = before.get(state_name) != after.get(state_name)
            if disposition == "completed" and not actual_changed:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_WRITE_MISSING", f"{effect_id} did not change its target.", f"$effects.{effect_id}"))
            if disposition != "completed" and actual_changed:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSED_WRITE", f"{effect_id} changed state despite refusal.", f"$effects.{effect_id}"))
            instances.append(
                {
                    "id": f"instance:{node_id}:{effect_id}",
                    "effectId": effect_id,
                    "owner": f"intent:{owner_suffix}",
                    "operationId": node_id,
                    "disposition": disposition,
                    "target": f"state:{state_name}",
                    "before": before.get(state_name),
                    "after": after.get(state_name),
                    "evidence": {
                        "operationReceipt": result,
                        "canonicalReconciliation": {"before": before, "after": after},
                        "visibleOutcome": {
                            "operationId": browser_id,
                            "visible": browser.get("visible"),
                            "before": browser_before,
                            "after": browser_after,
                        },
                    },
                    "status": "closed",
                }
            )

    direct_node = node_ops.get("candidate-direct-set") or {}
    direct_browser = browser_ops.get("candidate-browser-direct-set") or {}
    direct_clean = (
        (direct_node.get("result") or {}).get("ok") is False
        and (direct_node.get("result") or {}).get("code") == "INTENT_PROHIBITED"
        and direct_node.get("before") == direct_node.get("after")
        and (direct_browser.get("result") or {}).get("ok") is False
        and (direct_browser.get("result") or {}).get("code") == "INTENT_PROHIBITED"
        and direct_browser.get("before") == direct_browser.get("after")
        and str(direct_browser.get("visible")) == str((direct_browser.get("after") or {}).get("count"))
        and not any((item.get("owner") or {}).get("ref") == "intent:direct-set" for item in declared.values())
    )
    if not direct_clean:
        diagnostics.append(_diagnostic("MCEL_COUNTER_DIRECT_SET_EFFECT_LEAK", "Prohibited direct-set produced or implied a canonical effect.", "$effects.directSet"))

    acceptance_pass = acceptance.get("status") == "pass" and acceptance.get("passed") is True
    observation_pass = observation.get("status") == "pass" and observation.get("ok") is True
    if not acceptance_pass:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_ACCEPTANCE_MISSING", "Candidate acceptance evidence did not pass.", "$acceptance"))
    if not observation_pass:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_OBSERVATION_MISSING", "Candidate browser observation did not pass.", "$observation"))

    valid = not diagnostics and len(instances) == 6 and direct_clean
    return {
        "schema": EFFECT_REPORT_SCHEMA,
        "appId": "contract-counter",
        "status": "closed" if valid else "open",
        "valid": valid,
        "declaredEffectCount": len(declared),
        "effectInstanceCount": len(instances),
        "closedEffectInstanceCount": sum(item.get("status") == "closed" for item in instances),
        "unexplainedEffectCount": 0 if valid else len(diagnostics),
        "directSetCanonicalWriteObserved": not direct_clean,
        "instances": instances,
        "diagnostics": diagnostics,
        "authorities": {
            "acceptance": "pass" if acceptance_pass else "fail",
            "browserObservation": "pass" if observation_pass else "fail",
            "nodeEffectProbe": "pass" if set(node_ops) == required_node else "fail",
            "browserEffectProbe": "pass" if set(browser_ops) == required_browser else "fail",
        },
    }


def _publish_evidence(
    *,
    workspace: Path,
    output_directory: Path,
    report: Mapping[str, Any],
    effect_accounting: Mapping[str, Any],
    node_probe: Mapping[str, Any],
    browser_probe: Mapping[str, Any],
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    mappings = (
        (
            workspace / "runtime/reports/mcel-acceptance/apps/contract-counter",
            output_directory / "acceptance",
        ),
        (
            workspace / "runtime/reports/mcel-observation/apps/contract-counter",
            output_directory / "observation",
        ),
        (
            workspace / "runtime/reports/mcel-app-proof/apps/contract-counter",
            output_directory / "proof",
        ),
    )
    for source, target in mappings:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    effects = output_directory / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    _write_json(effects / "mcel-counter-effect-accounting-report.json", effect_accounting)
    _write_json(effects / "mcel-counter-node-effect-probe.json", node_probe)
    _write_json(effects / "mcel-counter-browser-effect-probe.json", browser_probe)


def _published_artifacts(output_directory: Path, repo: Path) -> dict[str, Any]:
    paths = {
        "acceptance": output_directory / "acceptance/mcel-acceptance-report.json",
        "browserObservation": output_directory / "observation/mcel-operation-observation-report.json",
        "effectAccounting": output_directory / "effects/mcel-counter-effect-accounting-report.json",
        "applicationProof": output_directory / "proof/mcel-app-proof-report.json",
    }
    return {
        name: {
            "path": _display_path(path, repo),
            "sha256": _sha256_path(path),
        }
        for name, path in paths.items()
    }


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CounterCandidateEvidenceError(f"Could not load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CounterCandidateEvidenceError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_path(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _failure(status: str, diagnostics: list[Mapping[str, Any]], message: str) -> CandidateEvidenceResult:
    diagnostics.append(_diagnostic("MCEL_COUNTER_CANDIDATE_EVIDENCE_SOURCE_INVALID", message, "$candidate"))
    return _result(False, status, diagnostics, {})


def _result(
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    report: Mapping[str, Any],
    output_directory: Path | None = None,
) -> CandidateEvidenceResult:
    return CandidateEvidenceResult(valid, status, report, tuple(diagnostics), output_directory)


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# MCEL Counter Candidate Evidence",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Semantic fingerprint: `{(report.get('candidate') or {}).get('semanticFingerprint', '')}`",
        f"- Source-binding fingerprint: `{(report.get('candidate') or {}).get('sourceBindingFingerprint', '')}`",
        "",
        "## Stages",
        "",
    ]
    for stage, value in (report.get("stages") or {}).items():
        lines.append(f"- {stage}: `{value.get('status')}`")
    lines.extend(
        [
            "",
            "## Authority",
            "",
            f"- Live authority: `{(report.get('authority') or {}).get('liveAuthority')}`",
            f"- Candidate promoted: `{str((report.get('authority') or {}).get('candidatePromoted')).lower()}`",
            f"- Evidence reused: `{str((report.get('authority') or {}).get('evidenceReused')).lower()}`",
            f"- Promotion eligible: `{str((report.get('authority') or {}).get('promotionEligible')).lower()}`",
            "",
        ]
    )
    return "\n".join(lines)
