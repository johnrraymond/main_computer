"""Native constrained-expression profile for Contract Workbench.

Wave 11 replaces every legacy opaque callback record in the repository-derived
Workbench Application IR with one versioned ``domain.call`` expression.  Each
call retains the former callback hash only as a compatibility identity so the
v1 application semantic fingerprint remains stable across the migration.

The operator profile is declarative.  It is consumed by the constrained-
expression analyzer and by the Workbench projection profile; it does not
execute JavaScript callbacks during authoring or proof.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, MutableMapping

APP_ID = "contract-workbench"
PROFILE_ID = "mcel.workbench.constrained-expression-profile.v1"
OPERATOR_VERSION = "v1"


def _state(name: str) -> dict[str, Any]:
    return {"kind": "state.read", "state": {"ref": f"state:{name}"}}


def _input(intent: str, name: str) -> dict[str, Any]:
    return {"kind": "input.read", "input": {"ref": f"input:{intent}.{name}"}}


def _context(name: str) -> dict[str, Any]:
    return {"kind": "context.read", "context": {"ref": f"context:{name}"}}


# One operator registration per former opaque expression root.  Keeping output
# slots separate makes types and repair diagnostics exact even when several
# slots were historically produced by one callback body.
SPECS: tuple[Mapping[str, Any], ...] = (
    {
        "locator": ("derivation", "derivation:canSubmit", "derive"),
        "operator": "operator:workbench.derive.can-submit",
        "context": "derivation",
        "resultType": {"kind": "boolean"},
        "arguments": {"draftName": _state("draftName"), "draftQuantity": _state("draftQuantity")},
        "description": "Return whether the current Workbench draft can be submitted.",
    },
    {
        "locator": ("derivation", "derivation:totalQuantity", "derive"),
        "operator": "operator:workbench.derive.total-quantity",
        "context": "derivation",
        "resultType": {"kind": "integer"},
        "arguments": {"contracts": _state("contracts")},
        "description": "Sum canonical contract quantities.",
    },
    {
        "locator": ("derivation", "derivation:visibleContracts", "derive"),
        "operator": "operator:workbench.derive.visible-contracts",
        "context": "derivation",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {
            "contracts": _state("contracts"),
            "filterText": _state("filterText"),
            "sortMode": _state("sortMode"),
        },
        "description": "Filter and deterministically sort the visible contract collection.",
    },
    {
        "locator": ("intent", "intent:add-contract", "ensures"),
        "operator": "operator:workbench.add-contract.ensures",
        "context": "postcondition",
        "resultType": {"kind": "boolean"},
        "arguments": {"before": _context("before"), "after": _context("after"), "payload": _context("payload")},
        "description": "Verify add-contract length and revision postconditions.",
    },
    {
        "locator": ("intent-transition", "intent:add-contract", "state:contracts"),
        "operator": "operator:workbench.add-contract.contracts",
        "context": "mutation-transition",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {
            "contracts": _state("contracts"),
            "nextContractId": _state("nextContractId"),
            "name": _input("add-contract", "name"),
            "quantity": _input("add-contract", "quantity"),
            "category": _input("add-contract", "category"),
        },
        "description": "Append the new canonical contract record.",
    },
    {
        "locator": ("intent-transition", "intent:add-contract", "state:nextContractId"),
        "operator": "operator:workbench.add-contract.next-id",
        "context": "mutation-transition",
        "resultType": {"kind": "integer", "minimum": 1},
        "arguments": {"nextContractId": _state("nextContractId")},
        "description": "Advance the deterministic contract identifier counter.",
    },
    {
        "locator": ("intent-transition", "intent:add-contract", "state:revision"),
        "operator": "operator:workbench.add-contract.revision",
        "context": "mutation-transition",
        "resultType": {"kind": "integer", "minimum": 0},
        "arguments": {"revision": _state("revision")},
        "description": "Advance the canonical Workbench revision after add-contract.",
    },
    {
        "locator": ("intent", "intent:clear-all", "ensures"),
        "operator": "operator:workbench.clear-all.ensures",
        "context": "postcondition",
        "resultType": {"kind": "boolean"},
        "arguments": {"before": _context("before"), "after": _context("after")},
        "description": "Verify clear-all empties the collection and advances revision.",
    },
    {
        "locator": ("intent-transition", "intent:clear-all", "state:contracts"),
        "operator": "operator:workbench.clear-all.contracts",
        "context": "mutation-transition",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {"contracts": _state("contracts")},
        "description": "Produce the empty canonical contract collection.",
    },
    {
        "locator": ("intent-transition", "intent:clear-all", "state:revision"),
        "operator": "operator:workbench.clear-all.revision",
        "context": "mutation-transition",
        "resultType": {"kind": "integer", "minimum": 0},
        "arguments": {"revision": _state("revision")},
        "description": "Advance the canonical Workbench revision after clear-all.",
    },
    {
        "locator": ("intent", "intent:remove-contract", "ensures"),
        "operator": "operator:workbench.remove-contract.ensures",
        "context": "postcondition",
        "resultType": {"kind": "boolean"},
        "arguments": {"before": _context("before"), "after": _context("after"), "payload": _context("payload")},
        "description": "Verify the selected contract is absent and revision advanced.",
    },
    {
        "locator": ("intent-transition", "intent:remove-contract", "state:contracts"),
        "operator": "operator:workbench.remove-contract.contracts",
        "context": "mutation-transition",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {"contracts": _state("contracts"), "contractId": _input("remove-contract", "contractId")},
        "description": "Remove one contract by stable key.",
    },
    {
        "locator": ("intent-transition", "intent:remove-contract", "state:revision"),
        "operator": "operator:workbench.remove-contract.revision",
        "context": "mutation-transition",
        "resultType": {"kind": "integer", "minimum": 0},
        "arguments": {"revision": _state("revision")},
        "description": "Advance the canonical Workbench revision after removal.",
    },
    {
        "locator": ("intent", "intent:request-quote", "commit"),
        "operator": "operator:workbench.request-quote.commit-transition",
        "context": "capability-reconciliation",
        "resultType": {"kind": "transition"},
        "arguments": {"state": _context("state"), "provisional": _context("provisional"), "payload": _context("payload")},
        "description": "Construct the single canonical quote reconciliation transition.",
    },
    {
        "locator": ("intent", "intent:request-quote", "ensures"),
        "operator": "operator:workbench.request-quote.ensures",
        "context": "postcondition",
        "resultType": {"kind": "boolean"},
        "arguments": {"before": _context("before"), "after": _context("after"), "payload": _context("payload")},
        "description": "Verify quote reconciliation commits one result and advances revision.",
    },
    {
        "locator": ("intent", "intent:request-quote", "reconcile"),
        "operator": "operator:workbench.request-quote.reconcile-event",
        "context": "capability-reconciliation",
        "resultType": {"kind": "transition"},
        "arguments": {"provisional": _context("provisional"), "event": _context("event"), "payload": _context("payload")},
        "description": "Reconcile one streamed quote event into provisional state.",
    },
    {
        "locator": ("intent", "intent:request-quote", "request"),
        "operator": "operator:workbench.request-quote.build-request",
        "context": "capability-request",
        "resultType": {"kind": "record"},
        "arguments": {"contracts": _state("contracts"), "contractId": _input("request-quote", "contractId")},
        "description": "Build the versioned quote-service request from canonical state.",
    },
    {
        "locator": ("intent-transition", "intent:request-quote", "state:contracts"),
        "operator": "operator:workbench.request-quote.contracts",
        "context": "capability-reconciliation",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {"contracts": _state("contracts"), "provisional": _context("provisional"), "payload": _context("payload")},
        "description": "Apply reconciled quote status and amount to the selected contract.",
    },
    {
        "locator": ("intent-transition", "intent:request-quote", "state:revision"),
        "operator": "operator:workbench.request-quote.revision",
        "context": "capability-reconciliation",
        "resultType": {"kind": "integer", "minimum": 0},
        "arguments": {"revision": _state("revision")},
        "description": "Advance the canonical Workbench revision after quote commit.",
    },
    {
        "locator": ("intent", "intent:update-quantity", "ensures"),
        "operator": "operator:workbench.update-quantity.ensures",
        "context": "postcondition",
        "resultType": {"kind": "boolean"},
        "arguments": {"after": _context("after"), "payload": _context("payload")},
        "description": "Verify the selected quantity was updated.",
    },
    {
        "locator": ("intent-transition", "intent:update-quantity", "state:contracts"),
        "operator": "operator:workbench.update-quantity.contracts",
        "context": "mutation-transition",
        "resultType": {"kind": "list", "items": {"kind": "object"}},
        "arguments": {
            "contracts": _state("contracts"),
            "contractId": _input("update-quantity", "contractId"),
            "quantity": _input("update-quantity", "quantity"),
        },
        "description": "Replace one contract quantity by stable key.",
    },
    {
        "locator": ("intent-transition", "intent:update-quantity", "state:revision"),
        "operator": "operator:workbench.update-quantity.revision",
        "context": "mutation-transition",
        "resultType": {"kind": "integer", "minimum": 0},
        "arguments": {"revision": _state("revision")},
        "description": "Advance the canonical Workbench revision after quantity update.",
    },
    {
        "locator": ("invariant", "invariant:contract-workbench.contract-keys-unique", "check"),
        "operator": "operator:workbench.invariant.contract-keys-unique",
        "context": "invariant",
        "resultType": {"kind": "boolean"},
        "arguments": {"contracts": _state("contracts")},
        "description": "Require unique stable contract keys.",
    },
    {
        "locator": ("invariant", "invariant:contract-workbench.contract-values-valid", "check"),
        "operator": "operator:workbench.invariant.contract-values-valid",
        "context": "invariant",
        "resultType": {"kind": "boolean"},
        "arguments": {"contracts": _state("contracts")},
        "description": "Require every canonical contract value to satisfy the Workbench schema.",
    },
    {
        "locator": ("invariant", "invariant:contract-workbench.contracts-array", "check"),
        "operator": "operator:workbench.invariant.contracts-array",
        "context": "invariant",
        "resultType": {"kind": "boolean"},
        "arguments": {"contracts": _state("contracts")},
        "description": "Require canonical contracts to remain a list.",
    },
    {
        "locator": ("invariant", "invariant:contract-workbench.revision-nonnegative", "check"),
        "operator": "operator:workbench.invariant.revision-nonnegative",
        "context": "invariant",
        "resultType": {"kind": "boolean"},
        "arguments": {"revision": _state("revision")},
        "description": "Require the canonical revision to remain a nonnegative integer.",
    },
)


def operator_records() -> tuple[Mapping[str, Any], ...]:
    """Return portable registry records consumed by the shared analyzer."""
    return tuple(
        {
            "id": str(spec["operator"]),
            "version": OPERATOR_VERSION,
            "inputTypes": {str(name): {"kind": "unknown"} for name in sorted((spec["arguments"] or {}).keys())},
            "resultType": copy.deepcopy(spec["resultType"]),
            "allowedContexts": [str(spec["context"])],
            "totality": "total",
            "description": str(spec["description"]),
        }
        for spec in SPECS
    )


def upgrade_application_ir(document: Mapping[str, Any]) -> dict[str, Any]:
    """Replace all 26 Workbench opaque roots with registered domain calls.

    The former opaque record is preserved under ``compatibility`` so semantic
    fingerprint v1 can prove identity across the representation migration.
    """
    candidate = copy.deepcopy(dict(document))
    app = candidate.get("application") if isinstance(candidate.get("application"), Mapping) else {}
    if app.get("appId") != APP_ID:
        return candidate

    replaced = 0
    for spec in SPECS:
        old = _locate(candidate, tuple(spec["locator"]))
        if not isinstance(old, Mapping) or old.get("kind") != "legacy.opaque-function":
            raise ValueError(f"Workbench native-expression locator did not resolve one opaque callback: {spec['locator']!r}")
        replacement = {
            "kind": "domain.call",
            "operator": {"ref": f"{spec['operator']}@{OPERATOR_VERSION}"},
            "arguments": copy.deepcopy(spec["arguments"]),
            "compatibility": {
                "semanticIdentity": "legacy-opaque-function-v1",
                "legacyOpaqueFunction": {
                    str(key): copy.deepcopy(value)
                    for key, value in old.items()
                    if str(key) != "migration"
                },
            },
        }
        _assign(candidate, tuple(spec["locator"]), replacement)
        replaced += 1

    if replaced != 26:
        raise ValueError(f"Workbench native-expression conversion replaced {replaced} callbacks; expected 26.")
    remaining = _count_opaque(candidate)
    if remaining:
        raise ValueError(f"Workbench native-expression conversion left {remaining} opaque callback(s).")

    migration = candidate.setdefault("migration", {})
    if isinstance(migration, MutableMapping):
        migration["sourceFamily"] = "official-vanilla-javascript-dsl"
        migration["expressionProfile"] = PROFILE_ID
        migration["opaqueCallbackCount"] = 0
        migration["portableIrProjectionComplete"] = True
        gaps = [
            str(value)
            for value in migration.get("knownGaps") or []
            if str(value)
            not in {
                "opaque-callbacks-require-constrained-expression-replacement",
                "migration-ir-bridge-not-final-authoring-surface",
            }
        ]
        migration["knownGaps"] = sorted(set(gaps))
    return candidate


def count_native_calls(document: Mapping[str, Any]) -> int:
    return _count_kind(document, "domain.call")


def count_opaque_callbacks(document: Mapping[str, Any]) -> int:
    return _count_opaque(document)


def _locate(document: MutableMapping[str, Any], locator: tuple[str, str, str]) -> Any:
    kind, semantic_id, slot = locator
    if kind == "derivation":
        node = _by_id(document.get("derivations"), semantic_id)
        return node.get(slot) if node else None
    if kind == "invariant":
        proof = document.get("proof") if isinstance(document.get("proof"), MutableMapping) else {}
        node = _by_id(proof.get("invariants"), semantic_id)
        return node.get(slot) if node else None
    if kind == "intent":
        node = _by_id(document.get("intents"), semantic_id)
        return node.get(slot) if node else None
    if kind == "intent-transition":
        node = _by_id(document.get("intents"), semantic_id)
        transition = node.get("transition") if node and isinstance(node.get("transition"), MutableMapping) else {}
        for step in transition.get("steps") or []:
            if isinstance(step, MutableMapping) and ((step.get("target") or {}).get("ref") == slot):
                return step.get("value")
    return None


def _assign(document: MutableMapping[str, Any], locator: tuple[str, str, str], value: Mapping[str, Any]) -> None:
    kind, semantic_id, slot = locator
    if kind == "derivation":
        _by_id(document.get("derivations"), semantic_id)[slot] = copy.deepcopy(value)
        return
    if kind == "invariant":
        proof = document.get("proof")
        _by_id(proof.get("invariants"), semantic_id)[slot] = copy.deepcopy(value)
        return
    if kind == "intent":
        _by_id(document.get("intents"), semantic_id)[slot] = copy.deepcopy(value)
        return
    if kind == "intent-transition":
        node = _by_id(document.get("intents"), semantic_id)
        for step in (node.get("transition") or {}).get("steps") or []:
            if isinstance(step, MutableMapping) and ((step.get("target") or {}).get("ref") == slot):
                step["value"] = copy.deepcopy(value)
                return
    raise KeyError(locator)


def _by_id(values: Any, semantic_id: str) -> MutableMapping[str, Any] | None:
    for value in values or []:
        if isinstance(value, MutableMapping) and value.get("id") == semantic_id:
            return value
    return None


def _count_opaque(value: Any) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == "legacy.opaque-function" else 0) + sum(
            _count_opaque(child) for key, child in value.items() if str(key) != "compatibility"
        )
    if isinstance(value, list):
        return sum(_count_opaque(child) for child in value)
    return 0


def _count_kind(value: Any, kind: str) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == kind else 0) + sum(_count_kind(child, kind) for child in value.values())
    if isinstance(value, list):
        return sum(_count_kind(child, kind) for child in value)
    return 0
