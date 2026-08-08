"""Guardrails for MCEL reference app and fixture wrappers.

Calculator, Counter, and Workbench are intentionally different reference cases,
but none of their app-specific wrappers should re-grow the projection/evidence/
proof/promotion mechanics now owned by the shared MCEL modules.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_COMPUTER = REPO_ROOT / "main_computer"

REFERENCE_APP_PROFILES = {
    "calculator": "mcel_calculator_host_bound_profile.py",
    "counter": "mcel_counter_reference_fixture_profile.py",
    "workbench": "mcel_workbench_reference_fixture_profile.py",
}

WRAPPER_IMPORTS = {
    # Calculator: real host-bound reference app.
    "mcel_calculator_candidate_projection.py": "mcel_host_bound_candidate_projection",
    "mcel_calculator_browser_observation.py": "mcel_host_bound_browser_observation",
    "mcel_calculator_candidate_evidence.py": "mcel_host_bound_candidate_evidence",
    "mcel_calculator_promotion_rehearsal.py": "mcel_host_bound_promotion_rehearsal",
    "mcel_calculator_parity.py": "mcel_host_bound_runtime_parity",
    "mcel_calculator_ir_native_proof.py": "mcel_host_bound_ir_native_proof",

    # Counter: small explicit-package reference fixture.
    "mcel_counter_candidate_projection.py": "mcel_explicit_package_candidate_projection",
    "mcel_counter_candidate_evidence.py": "mcel_explicit_package_candidate_evidence",
    "mcel_counter_compatibility.py": "mcel_explicit_package_compatibility",
    "mcel_counter_ir_native_proof.py": "mcel_explicit_package_ir_native_proof",
    "mcel_counter_promotion_rehearsal.py": "mcel_explicit_package_promotion_rehearsal",
    "mcel_counter_promotion.py": "mcel_explicit_package_promotion",

    # Workbench: profiled-package / authoring reference fixture.
    "mcel_workbench_candidate_projection.py": "mcel_profiled_package_candidate_projection",
    "mcel_workbench_candidate_evidence.py": "mcel_profiled_package_candidate_evidence",
    "mcel_workbench_ir_native_proof.py": "mcel_profiled_package_ir_native_proof",
    "mcel_workbench_promotion_rehearsal.py": "mcel_profiled_package_promotion_rehearsal",
    "mcel_workbench_promotion.py": "mcel_profiled_package_promotion",
}

# Wrappers are allowed to keep compatibility entry points and legacy helper
# names for tests/tools, but they should not become the home for generic MCEL
# mechanics again.
MAX_WRAPPER_LINES = 220

GENERIC_MECHANIC_IMPORTS_FORBIDDEN_IN_WRAPPERS = (
    "import shutil",
    "from shutil import",
    "import tempfile",
    "from tempfile import",
    "import zipfile",
    "from zipfile import",
)


def _source(path: Path) -> str:
    assert path.exists(), f"Expected MCEL wrapper/profile to exist: {path}"
    return path.read_text(encoding="utf-8")


def test_reference_app_profiles_exist_and_state_their_roles() -> None:
    for app_id, filename in REFERENCE_APP_PROFILES.items():
        source = _source(MAIN_COMPUTER / filename)
        assert "APP_ID" in source
        assert app_id in source
        assert "fixture" in source or "host-bound" in source or "Host-bound" in source


def test_app_specific_mcel_wrappers_delegate_to_generic_modules() -> None:
    for wrapper, generic_module in WRAPPER_IMPORTS.items():
        source = _source(MAIN_COMPUTER / wrapper)
        lines = source.splitlines()

        assert len(lines) <= MAX_WRAPPER_LINES, (
            f"{wrapper} has {len(lines)} lines; app wrappers should stay thin "
            f"and delegate mechanics to {generic_module}."
        )
        assert generic_module in source, f"{wrapper} should delegate to {generic_module}"

        for forbidden in GENERIC_MECHANIC_IMPORTS_FORBIDDEN_IN_WRAPPERS:
            assert forbidden not in source, (
                f"{wrapper} imports {forbidden!r}; workspace/file mechanics belong "
                "in shared MCEL modules, not app wrappers."
            )


def test_generic_mcel_mechanics_have_concrete_reference_users() -> None:
    generic_to_wrapper = {}
    for wrapper, generic_module in WRAPPER_IMPORTS.items():
        generic_to_wrapper.setdefault(generic_module, []).append(wrapper)

    for generic_module, wrappers in generic_to_wrapper.items():
        generic_path = MAIN_COMPUTER / f"{generic_module}.py"
        assert generic_path.exists(), f"Missing shared MCEL module {generic_module}.py"
        assert wrappers, f"{generic_module} should have at least one reference wrapper"

def test_reference_docs_name_patch_lifecycle_and_fixture_roles() -> None:
    roles_doc = REPO_ROOT / "pretty_docs" / "mcel-reference-app-and-fixture-roles.md"
    lifecycle_doc = REPO_ROOT / "pretty_docs" / "mcel-app-patching-lifecycle.md"
    status_doc = REPO_ROOT / "pretty_docs" / "mcel-status-and-roadmap.md"

    roles = _source(roles_doc)
    lifecycle = _source(lifecycle_doc)
    status = _source(status_doc)

    assert "Calculator is the real host-bound reference app" in roles
    assert "Counter is the small explicit-package reference fixture" in roles
    assert "Workbench is the profiled-package / authoring reference fixture" in roles
    assert "mcel-app-patching-lifecycle.md" in roles

    assert "Semantic feature patch" in lifecycle
    assert "Platform cleanup patch" in lifecycle
    assert "Unit Arithmetic v1" in lifecycle
    assert "mcel_counter_reference_fixture_profile.py" in lifecycle
    assert "mcel_workbench_reference_fixture_profile.py" in lifecycle

    assert "Reference app and fixture platform status" in status
    assert "Calculator: real host-bound reference app" in status
    assert "Counter:    explicit-package reference fixture" in status
    assert "Workbench:  profiled-package / authoring reference fixture" in status
    assert "mcel_host_bound_candidate_projection.py" in status
    assert "mcel_explicit_package_candidate_projection.py" in status
    assert "mcel_profiled_package_candidate_projection.py" in status
