#!/usr/bin/env python3
"""Gate Mother API-registry readiness without importing production code.

A registry is usable for contract tests only after it is specification-authored,
JSON-Schema valid, semantically reviewed, and marked ``normative-reviewed``.
Missing or draft registries are reported as specification-incomplete, not valid.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # pragma: no cover - dependency failure is the result
    raise SystemExit(
        "api-registry validation failed: install the 'jsonschema' package "
        "to apply api_registry.schema.json"
    ) from exc


MODULE_RE = re.compile(r"`(MOTHER-OFM-[A-Z]+-\d{3})`")
FUNCTION_RE = re.compile(r"`(MOTHER-OF-[A-Z]+-\d{3})`")
WILDCARD_RE = re.compile(r"[*?]")
BUILTIN_TYPES = {"bool", "bytes", "int", "None", "str"}


def fail(message: str, *, code: int = 1) -> None:
    print(f"api-registry validation failed: {message}", file=sys.stderr)
    raise SystemExit(code)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schema_error_message(errors: list[Any]) -> str:
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "<root>"
    return f"JSON Schema error at {location}: {first.message}"


def validate_semantic_guards(registry: dict[str, Any], repo: Path) -> None:
    if registry.get("status") != "normative-reviewed":
        fail(
            "registry is not normative-reviewed; module contract tests and "
            "production module work remain blocked",
            code=3,
        )

    governance = registry["governance"]
    if not governance.get("semantic_validation_complete"):
        fail("semantic_validation_complete is not true", code=3)

    expected_documents = {
        "mother.md",
        "mother-o.md",
        "mother-o-f.md",
        "mother-o-f-m.md",
    }
    if set(registry["source_documents"]) != expected_documents:
        fail(
            "source_documents must be exactly "
            f"{sorted(expected_documents)}"
        )

    reviewed_hashes = governance["reviewed_source_hashes"]
    for name in sorted(expected_documents):
        actual = sha256(repo / name)
        if reviewed_hashes.get(name) != actual:
            fail(
                f"reviewed source hash mismatch for {name}: "
                f"registry={reviewed_hashes.get(name)!r}, actual={actual}"
            )

    module_doc = (repo / "mother-o-f-m.md").read_text(encoding="utf-8")
    function_doc = (repo / "mother-o-f.md").read_text(encoding="utf-8")
    declared_modules = set(MODULE_RE.findall(module_doc))
    declared_functions = {
        item
        for item in FUNCTION_RE.findall(function_doc)
        if "-GAP-" not in item
    }

    modules = registry["modules"]
    registry_ids = [item["module_id"] for item in modules]
    if len(registry_ids) != len(set(registry_ids)):
        fail("duplicate module_id")
    if set(registry_ids) != declared_modules:
        missing = sorted(declared_modules - set(registry_ids))
        extra = sorted(set(registry_ids) - declared_modules)
        fail(f"module mismatch; missing={missing}, extra={extra}")
    if len(declared_modules) != 80:
        fail(f"expected 80 documented modules, found {len(declared_modules)}")
    if len(declared_functions) != 169:
        fail(f"expected 169 documented functionalities, found {len(declared_functions)}")

    required_rsl = {f"MOTHER-OF-RSL-{index:03d}" for index in range(1, 16)}
    if not required_rsl.issubset(declared_functions):
        fail(f"missing RSL functionality IDs: {sorted(required_rsl - declared_functions)}")

    defined_types = set(registry.get("type_contract", {}))
    seen_methods: set[tuple[str, str]] = set()
    for module in modules:
        module_id = module["module_id"]
        authority_class = module["authority_class"]
        methods = module["methods"]
        if not methods:
            fail(f"{module_id} has no public methods")

        for method in methods:
            identity = (module_id, method["method"])
            if identity in seen_methods:
                fail(f"duplicate method identity {module_id}.{method['method']}")
            seen_methods.add(identity)

            for code in method["error_codes"]:
                if WILDCARD_RE.search(code):
                    fail(f"{module_id}.{method['method']} uses wildcard error code {code!r}")

            for type_name in (method["input_type"], method["return_type"]):
                if type_name not in BUILTIN_TYPES and type_name not in defined_types:
                    fail(
                        f"{module_id}.{method['method']} references undefined "
                        f"type {type_name!r}"
                    )

            for template in method["durable_path_templates"]:
                if template.startswith("/") or ".." in Path(template).parts:
                    fail(
                        f"{module_id}.{method['method']} has non-canonical "
                        f"durable path template {template!r}"
                    )

            if authority_class in {"pure", "orchestrator"}:
                if method["durable_path_templates"] or method["durable_effect_types"]:
                    fail(
                        f"{module_id}.{method['method']} gives {authority_class} "
                        "a direct durable path/effect"
                    )

            if method["status"] == "contract-open":
                if method["required_lock_inputs"] or method["durable_effect_types"]:
                    fail(
                        f"{module_id}.{method['method']} is contract-open but "
                        "claims locks or durable effects"
                    )


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    registry_path = repo / "tools" / "mother" / "common" / "api_registry.yaml"
    schema_path = repo / "tools" / "mother" / "common" / "api_registry.schema.json"

    if not registry_path.exists():
        print(
            json.dumps(
                {
                    "registry": str(registry_path.relative_to(repo)),
                    "status": "absent",
                    "contract_tests_ready": False,
                    "reason": "specification-authored callable registry has not been populated",
                },
                sort_keys=True,
            )
        )
        return 3

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot parse registry: {exc}")

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot parse JSON Schema: {exc}")

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(registry), key=lambda item: tuple(str(part) for part in item.absolute_path))
    if errors:
        fail(schema_error_message(errors))

    validate_semantic_guards(registry, repo)

    print(
        json.dumps(
            {
                "registry": str(registry_path.relative_to(repo)),
                "modules": len(registry["modules"]),
                "methods": sum(len(item["methods"]) for item in registry["modules"]),
                "functionalities": 169,
                "status": "normative-reviewed",
                "contract_tests_ready": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
