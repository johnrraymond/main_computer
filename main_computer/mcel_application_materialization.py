"""In-memory materialization for DSL-authoritative MCEL application packages.

Authored package directories contain only durable source. Generated compatibility
contracts and normalized definitions are reconstructed on demand for validation,
runtime projection, evidence, and promotion compatibility. Nothing in this module
writes into ``mcel_apps``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_counter_candidate_projection import generate_counter_contracts
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import project_calculator_ir
from main_computer.mcel_projection_profiles.contract_workbench_v1 import project_workbench_ir

GENERATED_DIRECTORY_NAMES = frozenset({"contracts", "generated"})
GENERATED_FILE_NAMES = frozenset({"mcel.generated.json"})

_COUNTER_SOURCE_BINDING = "sha256:54e16c919103023872d62eb258871d0d61b65a5754534c0bd85bb122c4a3cfa2"
_WORKBENCH_SOURCE_BINDING = "sha256:d7c9d921ca19026dcf6366c762dad26bab3822790961701b189d06f28b6a92b1"


def is_generated_source_tree_path(relative: str) -> bool:
    parts = Path(relative).parts
    return bool(parts and (parts[0] in GENERATED_DIRECTORY_NAMES or relative in GENERATED_FILE_NAMES))


def materialize_generated_package_files(
    repo_root: Path,
    package_path: Path,
    physical_text_files: Mapping[str, str],
) -> dict[str, bytes]:
    """Return generated package files for a DSL-authored logical package.

    Legacy packages return an empty mapping. Promoted and explicitly shadowed
    DSL packages are projected in memory; the materialized file bytes are
    deterministic and are used as a virtual overlay by package discovery.
    """

    manifest = _manifest(physical_text_files)
    authoring = manifest.get("authoring") if isinstance(manifest.get("authoring"), Mapping) else {}
    authoring_status = str(authoring.get("status") or "")
    if authoring_status not in {"dsl-authoritative", "dsl-shadow"}:
        return {}
    app_id = str(manifest.get("appId") or package_path.name)
    source_rel = str(authoring.get("source") or "application.js")
    source = package_path / source_rel
    if not source.is_file():
        raise ValueError(f"DSL-authoritative package {app_id!r} is missing {source_rel}.")

    compiled = compile_dsl_application(source, write_candidate=False)
    if not compiled.valid or compiled.normalized_ir is None:
        details = "; ".join(str(item.get("summary") or item.get("code")) for item in compiled.diagnostics)
        raise ValueError(f"Could not compile DSL-authoritative package {app_id!r}: {details}")

    if app_id == "contract-counter":
        generated = generate_counter_contracts(compiled.normalized_ir)
        generated["mcel.generated.json"] = _ownership_bytes(
            app_id=app_id,
            generator="mcel.counter.explicit-projection.v1",
            semantic_fingerprint=str(compiled.semantic_fingerprint),
            source_binding_fingerprint=_COUNTER_SOURCE_BINDING,
            generated=generated,
            version="mcel-counter-promotion-rehearsal-wave6",
        )
        return generated

    if app_id == "contract-workbench":
        projection = project_workbench_ir(compiled.normalized_ir)
        generated = dict(projection.files)
        generated["mcel.generated.json"] = _ownership_bytes(
            app_id=app_id,
            generator="mcel.workbench.portable-ir-projection.v1",
            semantic_fingerprint=str(compiled.semantic_fingerprint),
            source_binding_fingerprint=_WORKBENCH_SOURCE_BINDING,
            generated=generated,
            version="mcel-app-promotion-rehearsal-wave12",
        )
        return generated

    if app_id == "calculator":
        projection = project_calculator_ir(compiled.normalized_ir)
        generated = dict(projection.files)
        generated["mcel.generated.json"] = _ownership_bytes(
            app_id=app_id,
            generator=projection.profile_id,
            semantic_fingerprint=str(compiled.semantic_fingerprint),
            source_binding_fingerprint=str(compiled.source_binding_fingerprint),
            generated=generated,
            version=(
                "mcel-calculator-promotion-rehearsal-v1"
                if authoring_status == "dsl-authoritative"
                else "mcel-calculator-shadow-projection-v1"
            ),
        )
        return generated

    raise ValueError(
        f"DSL-authoritative package {app_id!r} has no registered in-memory projection materializer."
    )


def _manifest(text_files: Mapping[str, str]) -> Mapping[str, Any]:
    try:
        value = json.loads(text_files.get("mcel.app.json", ""))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, Mapping) else {}


def _ownership_bytes(
    *,
    app_id: str,
    generator: str,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    generated: Mapping[str, bytes],
    version: str,
) -> bytes:
    records = [
        {
            "generator": generator,
            "path": relative,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        for relative, content in sorted(generated.items())
        if relative != "mcel.generated.json"
    ]
    payload = {
        "appId": app_id,
        "generatedArtifactsAreDerived": True,
        "generatedFiles": records,
        "manualEditsProhibited": True,
        "schema": "mcel.generated-file-ownership.v1",
        "sourceAuthority": {
            "kind": "mcel.dsl.v1",
            "path": "application.js",
            "semanticFingerprint": semantic_fingerprint,
            "sourceBindingFingerprint": source_binding_fingerprint,
        },
        "version": version,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
