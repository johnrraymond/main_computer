"""P5.3c read-only registry extraction contract verification.

    python -m tools.hub_registry_json_verify

No Hub connections, file-registry mutations, payments, or FDB transaction.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from main_computer import hub, hub_registry_json, hub_registry_shared
from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry

# SHA-256 of ast.get_source_segment(class HubRegistry) in the P5.3b source.
# Unlike ast.dump(), the source fingerprint is stable across Python 3.12/3.13.
EXPECTED_CLASS_SOURCE_SHA256 = "bd3cc7bb5cb1dbc975d8435842f722985dfd44beb5a17c24bd6973805a39b2e4"


def verify_contract() -> dict[str, object]:
    source=Path(hub_registry_json.__file__).read_text(encoding="utf-8")
    parsed=ast.parse(source)
    klass=next(item for item in parsed.body if isinstance(item,ast.ClassDef) and item.name=="HubRegistry")
    observed=hashlib.sha256(ast.get_source_segment(source,klass).encode("utf-8")).hexdigest()
    checks={
        "legacy_registry_import_preserved": hub.HubRegistry is hub_registry_json.HubRegistry,
        "dev_test_fdb_base_is_extracted_registry": ExperimentalFoundationDbRegistry.__bases__[0] is hub_registry_json.HubRegistry,
        "shared_worker_model_identity": hub.HubWorker is hub_registry_shared.HubWorker,
        "registry_class_body_unchanged": observed == EXPECTED_CLASS_SOURCE_SHA256,
        "registry_no_http_or_fdb_dependencies": all(
            node.module not in {"main_computer.hub", "main_computer.exp_fdb_hub", "main_computer.exp_fdb_hub_state", "main_computer.stable_hub"}
            for node in ast.walk(parsed) if isinstance(node,ast.ImportFrom)
        ),
    }
    return {"ok":all(checks.values()),"scope":"read-only, no Hub request, FDB transaction or payment", "extracted_module":"main_computer.hub_registry_json",**checks,
            "http_server_classes_still_in":"main_computer.hub"}


def main() -> int:
    result=verify_contract()
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
