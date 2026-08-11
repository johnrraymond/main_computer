from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
)
from main_computer.mcel_application_packages import (
    ApplicationPackageRecord,
    build_application_package_catalog,
)
from main_computer.mcel_application_runtime_projection import (
    ApplicationRuntimeProjection,
    build_runtime_projection_set,
)




@dataclass(frozen=True)
class ApplicationPackageCase:
    """Fixture-neutral view of one valid application package record."""

    app_id: str
    record: ApplicationPackageRecord
    repo_root: Path

    @property
    def package_path(self) -> Path:
        return self.repo_root / self.record.package_root

    @property
    def directory_name(self) -> str:
        return self.record.directory_name


@dataclass(frozen=True)
class DslSemanticRuntimeAppCase:
    """Fixture-neutral view of one DSL-authored semantic-runtime app package."""

    app_id: str
    record: ApplicationPackageRecord
    repo_root: Path

    @property
    def package_path(self) -> Path:
        return self.repo_root / self.record.package_root

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self.record.files["mcel.app.json"].decode("utf-8"))

    @property
    def manifest_authoring(self) -> dict[str, Any]:
        return dict(self.manifest.get("authoring") or {})

    @property
    def has_surface_bundle_contract(self) -> bool:
        return str(self.record.contracts.get("surfaceBundle") or "").replace("\\", "/").endswith(
            "/contracts/surface-bundle.json"
        )

    @property
    def surface_bundle(self) -> dict[str, Any] | None:
        payload = self.record.files.get("contracts/surface-bundle.json")
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))


def is_dsl_semantic_runtime_record(record: ApplicationPackageRecord) -> bool:
    if not record.valid or not record.app_id:
        return False
    manifest = json.loads(record.files["mcel.app.json"].decode("utf-8"))
    authoring = dict(manifest.get("authoring") or {})
    conformance = dict(record.conformance or {})
    current_mode = str(conformance.get("currentMode") or conformance.get("current_mode") or "")
    target_mode = str(conformance.get("targetMode") or conformance.get("target_mode") or "")
    return (
        authoring.get("status") == "dsl-authoritative"
        and str(authoring.get("source") or "").replace("\\", "/").endswith("application.js")
        and "semantic-runtime-proven" in {current_mode, target_mode}
    )


def discover_dsl_semantic_runtime_app_cases(repo_root: Path) -> list[DslSemanticRuntimeAppCase]:
    """Discover app cases by package metadata instead of by fixture app names."""

    catalog = build_application_package_catalog(repo_root)
    assert catalog.ok is True
    cases = [
        DslSemanticRuntimeAppCase(str(record.app_id), record, repo_root.resolve())
        for record in catalog.packages
        if is_dsl_semantic_runtime_record(record)
    ]
    return sorted(cases, key=lambda case: case.app_id)


def surface_bundle_cases(repo_root: Path) -> list[DslSemanticRuntimeAppCase]:
    return [
        case
        for case in discover_dsl_semantic_runtime_app_cases(repo_root)
        if case.has_surface_bundle_contract
    ]


def missing_surface_bundle_cases(repo_root: Path) -> list[DslSemanticRuntimeAppCase]:
    return [
        case
        for case in discover_dsl_semantic_runtime_app_cases(repo_root)
        if not case.has_surface_bundle_contract
    ]


def runtime_projections_by_app(repo_root: Path) -> dict[str, ApplicationRuntimeProjection]:
    projection_set = build_runtime_projection_set(repo_root)
    return {projection.app_id: projection for projection in projection_set.projections}


def browser_surface_bundles_by_app(repo_root: Path) -> dict[str, dict[str, Any]]:
    payload = build_repository_browser_catalog_payload(repo_root)
    return dict(payload.get("surfaceBundles") or {})




def profile_backed_surface_bundle_cases(repo_root: Path) -> list[DslSemanticRuntimeAppCase]:
    """Discover promoted DSL apps whose generic authoring profile can be dispatched.

    This intentionally selects by capabilities instead of by reference-app names.  It is
    used by retirement tests that replace old fixture-specific promotion coverage with a
    real promoted app that has package metadata, a browser surface bundle, and a generic
    authoring profile.
    """

    from main_computer.mcel_app_authoring_profiles import (
        AppAuthoringProfileError,
        get_app_authoring_profile,
    )

    cases: list[DslSemanticRuntimeAppCase] = []
    for case in surface_bundle_cases(repo_root):
        try:
            profile = get_app_authoring_profile(case.app_id)
        except AppAuthoringProfileError:
            continue
        if not profile.promotion_supported or not profile.promotion_rehearsal_supported:
            continue
        if case.manifest_authoring.get("status") != "dsl-authoritative":
            continue
        cases.append(case)
    return cases


def require_profile_backed_surface_bundle_case(repo_root: Path) -> DslSemanticRuntimeAppCase:
    cases = profile_backed_surface_bundle_cases(repo_root)
    assert cases, "expected at least one promoted DSL app with a generic authoring profile"
    return cases[0]


def authoring_profile_for_case(case: DslSemanticRuntimeAppCase):
    """Return the registered generic authoring profile for a discovered app case."""

    from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile

    return get_app_authoring_profile(case.app_id)


def package_file_snapshot(package_path: Path) -> dict[str, bytes]:
    """Capture a package tree so tests can prove candidate work is non-mutating."""

    return {
        path.relative_to(package_path).as_posix(): path.read_bytes()
        for path in package_path.rglob("*")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
    }


def package_record_for_case(case: DslSemanticRuntimeAppCase) -> ApplicationPackageRecord:
    return case.record


def synthetic_host_bound_browser_parity_probe_for_case(case: DslSemanticRuntimeAppCase):
    """Build a deterministic browser-parity probe for generic authority tests.

    The probe intentionally exercises the generic authority boundary without
    opening a browser or depending on reference fixture mechanics.  It derives
    intent coverage from the discovered package IR and marks every generated
    runtime binding as observed.
    """

    from main_computer.mcel_dsl_compiler import compile_dsl_application

    profile = authoring_profile_for_case(case)
    compiled = compile_dsl_application(case.package_path / "application.js", write_candidate=False)
    assert compiled.valid is True and compiled.normalized_ir is not None
    ir_payload = compiled.normalized_ir
    ownership = json.loads(case.record.files.get("mcel.generated.json", b"{}").decode("utf-8") or "{}")
    source_authority = dict(ownership.get("sourceAuthority") or {})
    semantic_fingerprint = getattr(compiled, "semantic_fingerprint", None) or source_authority.get("semanticFingerprint")
    source_binding_fingerprint = getattr(compiled, "source_binding_fingerprint", None) or source_authority.get("sourceBindingFingerprint")
    intents = []
    for intent in ir_payload.get("intents") or []:
        intent_id = str(intent.get("id") or "")
        source_name = str(intent.get("sourceName") or intent_id.rsplit(".", 1)[-1].removeprefix("intent:"))
        if intent_id and source_name:
            intents.append((source_name, intent_id))

    assert intents, f"{case.app_id} must declare intents for generic IR-native proof coverage"

    generated_bindings = {
        name: {
            "intentId": intent_id,
            "runtimeMethod": name,
            "lane": "synthetic-host-bound-runtime",
            "risk": "fixture-neutral-proof",
            "effectRefs": [],
        }
        for name, intent_id in intents
    }
    runtime_binding_checks = {name: True for name in generated_bindings}
    local_provider_free = {name: True for name in generated_bindings}

    def _probe(_repo: Path, _headed: bool, operation_prefix: str) -> dict[str, Any]:
        return {
            "schema": "mcel.synthetic-host-bound-browser-parity-probe.v1",
            "status": "pass",
            "valid": True,
            "appId": case.app_id,
            "operationPrefix": operation_prefix,
            "projectionProfile": profile.projection_profile,
            "semanticFingerprint": semantic_fingerprint or "sha256:synthetic-semantic",
            "sourceBindingFingerprint": source_binding_fingerprint or "sha256:synthetic-binding",
            "intentCount": len(generated_bindings),
            "generatedBindings": generated_bindings,
            "runtimeBindingChecks": runtime_binding_checks,
            "localProviderFree": local_provider_free,
            "capabilityAccounting": {
                "schema": "mcel.synthetic-capability-accounting.v1",
                "status": "closed",
                "closedIntentEffectCount": len(generated_bindings),
            },
            "checks": {
                "runtimeProjectionHostBound": True,
                "browserCatalogHostBound": True,
            },
            "authority": {
                "promotionEligible": True,
                "freshChromiumObservation": True,
                "legacySemanticAdapterRetired": True,
            },
            "browserObservation": {
                "schema": "mcel.synthetic-browser-observation.v1",
                "status": "pass",
                "observedIntentCount": len(generated_bindings),
                "checks": {"allPassed": True},
            },
        }

    return _probe


def assert_surface_bundle_contract_shape(case: DslSemanticRuntimeAppCase) -> dict[str, Any]:
    bundle = case.surface_bundle
    assert bundle is not None
    assert bundle["schema"] == "mcel.application-surface-bundle.v1"
    assert bundle["appId"] == case.app_id
    assert bundle["surfaceId"]
    assert bundle["contractId"]

    semantic_surface = bundle["semanticSurface"]
    layout_grammar = bundle["layoutGrammar"]
    assert semantic_surface["id"]
    assert layout_grammar["id"]

    regions = semantic_surface.get("regions") or []
    controls = semantic_surface.get("controls") or []
    layout_regions = layout_grammar.get("regions") or []
    layout_constraints = layout_grammar.get("constraints") or []

    assert regions, f"{case.app_id} must declare semantic regions"
    assert controls, f"{case.app_id} must declare semantic controls"
    assert layout_regions, f"{case.app_id} must declare layout regions"
    assert layout_constraints, f"{case.app_id} must declare layout constraints"

    region_ids = [region.get("id") for region in regions]
    control_ids = [control.get("id") for control in controls]
    layout_region_ids = [region.get("id") for region in layout_regions]
    constraint_ids = [constraint.get("id") for constraint in layout_constraints]

    assert len(region_ids) == len(set(region_ids))
    assert len(control_ids) == len(set(control_ids))
    assert len(layout_region_ids) == len(set(layout_region_ids))
    assert len(constraint_ids) == len(set(constraint_ids))

    assert any(region.get("primary") is True for region in regions)
    assert all(control.get("intent") or control.get("intentId") for control in controls)
    return bundle

def discover_valid_application_package_cases(repo_root: Path) -> list[ApplicationPackageCase]:
    """Discover valid application packages without depending on fixture app names."""

    root = repo_root.resolve()
    catalog = build_application_package_catalog(root)
    assert catalog.ok is True
    return [
        ApplicationPackageCase(str(record.app_id), record, root)
        for record in sorted(catalog.packages, key=lambda item: str(item.app_id))
        if record.valid and record.app_id
    ]


def expected_application_package_ids(repo_root: Path) -> set[str]:
    return {case.app_id for case in discover_valid_application_package_cases(repo_root)}


def self_contained_runtime_package_cases(repo_root: Path) -> list[ApplicationPackageCase]:
    """Packages that carry their own browser document/script/style.

    These are useful as copyable fixtures for catalog/projection safety checks,
    but callers should not care which app currently satisfies that capability.
    """

    cases: list[ApplicationPackageCase] = []
    for case in discover_valid_application_package_cases(repo_root):
        package_path = case.package_path
        if (
            case.record.runtime.get("document")
            and case.record.runtime.get("script")
            and case.record.runtime.get("style")
            and case.record.acceptance_bindings
        ):
            cases.append(case)
    return cases


def reference_self_contained_runtime_package_case(repo_root: Path) -> ApplicationPackageCase:
    cases = self_contained_runtime_package_cases(repo_root)
    assert cases, "Expected at least one self-contained runtime package test case."
    return cases[0]


def copy_reference_self_contained_runtime_package(
    repo_root: Path,
    target_root: Path,
    *,
    directory_name: str | None = None,
) -> tuple[ApplicationPackageCase, Path]:
    """Copy one capable package into a temporary repo without naming fixture apps.

    If no checked-in self-contained package remains after reference-fixture
    deletion, render the canonical scaffolding template into the temporary repo.
    The generated sample package keeps package/catalog safety tests alive without
    reintroducing Contract Counter or Contract Workbench as source fixtures.
    """

    cases = self_contained_runtime_package_cases(repo_root)
    if cases:
        case = cases[0]
        destination_name = directory_name or case.directory_name
        destination = target_root / "mcel_apps" / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(case.package_path, destination)
        return case, destination

    from main_computer.mcel_scaffolding import render_package_files

    sample_app_id = "sample-app"
    destination_name = directory_name or sample_app_id
    destination = target_root / "mcel_apps" / destination_name
    destination.mkdir(parents=True, exist_ok=False)
    _template, _title, files = render_package_files(sample_app_id, title="Sample Application")
    for relative, text in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    catalog = build_application_package_catalog(target_root)
    record = next(item for item in catalog.packages if item.package_root == f"mcel_apps/{destination_name}")
    return ApplicationPackageCase(str(record.app_id), record, target_root.resolve()), destination

def package_acceptance_cases(repo_root: Path) -> list[ApplicationPackageCase]:
    """Discover valid packages that declare package-local acceptance bindings."""

    return [
        case
        for case in discover_valid_application_package_cases(repo_root)
        if case.record.acceptance_bindings and case.record.requirements and case.record.tests_root
    ]


def require_package_acceptance_case(repo_root: Path) -> ApplicationPackageCase:
    cases = package_acceptance_cases(repo_root)
    assert cases, "Expected at least one package with package-local acceptance bindings."
    return cases[0]


def package_case_by_app_id(repo_root: Path, app_id: str) -> ApplicationPackageCase:
    matches = [
        case for case in discover_valid_application_package_cases(repo_root)
        if case.app_id == app_id
    ]
    assert len(matches) == 1, f"Expected one valid package case for {app_id!r}."
    return matches[0]

