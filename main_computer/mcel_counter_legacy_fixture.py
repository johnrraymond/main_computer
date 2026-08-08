"""Contract Counter reference-fixture metadata.

Counter is intentionally retained as the small explicit-package MCEL conformance
fixture.  The files under ``mcel_apps/contract-counter`` are fixture input for
legacy explicit-package import/projection tests, not active product code.  This
module keeps that role explicit so generic MCEL tooling can depend on stable
fixture facts without importing operational wrappers.
"""

from __future__ import annotations

from pathlib import Path

APP_ID = "contract-counter"
FIXTURE_ROLE = "mcel.reference-fixture.explicit-package.counter.v1"
LEGACY_PACKAGE_ROLE = "mcel.reference-fixture.explicit-package.legacy-source.v1"

LEGACY_IMPORTER_ID = "mcel.counter.legacy-importer"
COUNTER_LEGACY_IMPORT_REPORT_SCHEMA = "mcel.counter-legacy-import-report.v1"
COUNTER_LEGACY_RUNTIME_RESULT_SCHEMA = "mcel.counter-legacy-runtime-result.v1"
COUNTER_LEGACY_IMPORTER_VERSION = "mcel-counter-legacy-importer-wave3"
COUNTER_FRONTEND_ID = "legacy.explicit-package.contract-counter"
COUNTER_FRONTEND_VERSION = "mcel-explicit-package-v1"

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COUNTER_ROOT = REPOSITORY_ROOT / "mcel_apps" / APP_ID
NODE_RUNTIME = Path(__file__).resolve().with_name("mcel_counter_legacy_runtime.js")

SOURCE_FILES = (
    "contracts/acceptance.js",
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/layout.js",
    "contracts/observation.js",
    "contracts/surface.js",
    "requirements.md",
)


def legacy_fixture_metadata() -> dict[str, object]:
    """Return stable metadata describing Counter's role as a legacy fixture."""

    return {
        "appId": APP_ID,
        "fixtureRole": FIXTURE_ROLE,
        "legacyPackageRole": LEGACY_PACKAGE_ROLE,
        "legacyImporterId": LEGACY_IMPORTER_ID,
        "frontendId": COUNTER_FRONTEND_ID,
        "frontendVersion": COUNTER_FRONTEND_VERSION,
        "sourceFiles": list(SOURCE_FILES),
    }
