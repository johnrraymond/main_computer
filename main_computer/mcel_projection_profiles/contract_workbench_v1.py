"""Deterministic Workbench compatibility projection from canonical Application IR.

This profile retains only projection policy and executable callback implementations.
It reconstructs the legacy-compatible logical package entirely in memory; no generated
Workbench snapshot is read from the repository.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from main_computer.mcel_application_definition_ir import definition_to_application_ir
from main_computer.mcel_application_definition_normalizer import render_application_definition_files
from main_computer.mcel_application_ir import compare_application_ir, validate_application_ir
from main_computer.mcel_constrained_expression import DomainOperatorRegistry
from main_computer.mcel_workbench_expression_profile import (
    count_native_calls,
    count_opaque_callbacks,
    operator_records,
)

APP_ID = "contract-workbench"
PROFILE_ID = "mcel.workbench.portable-ir-projection.v1"
PROFILE_SCHEMA = "mcel.application-projection-profile.v1"
EXPECTED_SEMANTIC_FINGERPRINT = "sha256:3450eddcd5b67687fc09ff7589221fff5ef176efcc2d54231a9b43e2268ca78e"
EXPECTED_DEFINITION_FINGERPRINT = "sha256:6cb3c6d27a351fdd12e9d9e714e70ba75ed87468bc56815a54bf2078784c408d"
LEGACY_DEFINITION_SOURCE_SHA256 = "1d3cebff74ea9a8516eb3e54a8e7ffcc815a11bcc0c891ec36737b1a69e040e5"
SOURCE_REFERENCE = "mcel_apps/contract-workbench/application.js"
NORMALIZED_REFERENCE = "mcel_apps/contract-workbench/generated/mcel.application.normalized.json"
EXPECTED_OPERATOR_COUNT = 26
EXPECTED_OPERATOR_REGISTRY_FINGERPRINT = "sha256:e58c7198dc22ab887572379cef6147fb19e4cd06f63c20f996125831d5981dce"

GENERATED_PATHS = (
    "generated/mcel.application.normalized.json",
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/adapter.js",
    "contracts/surface.js",
    "contracts/layout.js",
    "contracts/acceptance.js",
    "contracts/observation.js",
)

INVARIANT_ORDER = ['contract-workbench.invariant.contracts-array',
 'contract-workbench.invariant.contract-keys-unique',
 'contract-workbench.invariant.contract-values-valid',
 'contract-workbench.invariant.revision-nonnegative']
ACCEPTANCE_ORDER = ['contract-workbench.acceptance.add',
 'contract-workbench.acceptance.validation',
 'contract-workbench.acceptance.remove',
 'contract-workbench.acceptance.update',
 'contract-workbench.acceptance.clear-all',
 'contract-workbench.acceptance.filter-sort',
 'contract-workbench.acceptance.quote',
 'contract-workbench.acceptance.cancel',
 'contract-workbench.acceptance.quote-supersession',
 'contract-workbench.acceptance.quote-parallel',
 'contract-workbench.acceptance.stale',
 'contract-workbench.acceptance.duplicate',
 'contract-workbench.acceptance.prohibited',
 'contract-workbench.acceptance.multi-instance']
SURFACE_NODE_ORDER = ['contract-workbench.draft-name',
 'contract-workbench.draft-quantity',
 'contract-workbench.draft-category',
 'contract-workbench.add-control',
 'contract-workbench.validation',
 'contract-workbench.total-quantity',
 'contract-workbench.visible-count',
 'contract-workbench.filter-text',
 'contract-workbench.sort-mode',
 'contract-workbench.empty-state',
 'contract-workbench.items',
 'contract-workbench.clear-control',
 'contract-workbench.latest-receipt']
PREFLIGHT_HASHES = {'add-contract': 'sha256:1d8590a48a7e01ccf461570d18b888957bd46d42e26865bd14bb843e1a142136',
 'remove-contract': 'sha256:85feb50d805ec2533576e5f005ea9c2299d2d3f3b9c71b34656fb8a3c54eb9cc',
 'update-quantity': 'sha256:6a2921c43176e8cf315348765bbc1cce6d74c1434deb39f4da265416aa368352'}
PROVISIONAL_PATHS = {'request-quote': 'quoteProgress'}
SURFACE_NODE_SOURCE_POLICY = {
    "contract-workbench.validation": {
        "kind": "projection-source",
        "path": "message",
        "sourceKind": "latest-receipt",
    }
}
MULTI_INSTANCE_POLICY = {'isolation': ['canonical-state', 'local-state', 'provisional-state', 'operation-ledger', 'receipts', 'dom-roots'],
 'minimumInstances': 2,
 'required': True}
REQUIRED_RUNTIME_FEATURES = ['capability-operation-runtime',
 'conditional-projection',
 'control-payload-extraction',
 'derived-state',
 'dynamic-browser-observation',
 'dynamic-input-binding',
 'dynamic-item-control-binding',
 'dynamic-property-projection',
 'keyed-collection-reconciliation',
 'multi-instance-proof',
 'operation-cancellation',
 'operation-concurrency-policy',
 'provisional-state',
 'provisional-state-runtime',
 'renderer-local-state']
OBSERVATIONS_POLICY = [{'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': None,
  'expect': {'$undefined': True},
  'fields': {},
  'id': 'contract-workbench.observe.total',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.total-quantity',
  'normalization': 'string',
  'observationKind': 'property',
  'property': 'textContent',
  'requireIsolated': [],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': 'totalQuantity'},
 {'compareToLatestReceiptPath': 'message',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': None,
  'expect': {'$undefined': True},
  'fields': {},
  'id': 'contract-workbench.observe.validation',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.validation',
  'normalization': '',
  'observationKind': 'conditional',
  'property': '',
  'requireIsolated': [],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': ''},
 {'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': {'path': 'visibleContracts', 'predicate': 'empty'},
  'expect': {'$undefined': True},
  'fields': {},
  'id': 'contract-workbench.observe.empty',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.empty-state',
  'normalization': '',
  'observationKind': 'conditional',
  'property': '',
  'requireIsolated': [],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': ''},
 {'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': None,
  'expect': {'$undefined': True},
  'fields': {'category': 'category',
             'name': 'name',
             'quantity': 'quantity',
             'quoteAmount': 'quoteAmount',
             'quoteStatus': 'quoteStatus'},
  'id': 'contract-workbench.observe.items',
  'keyPath': 'id',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.items',
  'normalization': '',
  'observationKind': 'collection',
  'property': '',
  'requireIsolated': [],
  'requireItemControls': ['update-quantity', 'remove-contract', 'request-quote', 'cancel-quote'],
  'requireOrderMatch': True,
  'source': 'browser-dom',
  'statePath': 'visibleContracts'},
 {'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': 'quoteProgress',
  'compareToStatePredicate': None,
  'expect': {'$undefined': True},
  'fields': {},
  'id': 'contract-workbench.observe.progress',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.items',
  'normalization': '',
  'observationKind': 'provisional',
  'property': '',
  'requireIsolated': [],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': ''},
 {'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': True,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': None,
  'expect': {'$undefined': True},
  'fields': {},
  'id': 'contract-workbench.observe.receipt',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 0,
  'nodeId': 'contract-workbench.latest-receipt',
  'normalization': '',
  'observationKind': 'receipt',
  'property': '',
  'requireIsolated': [],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': ''},
 {'compareToLatestReceiptPath': '',
  'compareToOperationReceipt': False,
  'compareToProvisionalStatePath': '',
  'compareToStatePredicate': None,
  'expect': {'isolatedReceipts': True, 'isolatedState': True},
  'fields': {},
  'id': 'contract-workbench.observe.multi-instance',
  'keyPath': '',
  'kind': 'observation',
  'minimumInstances': 2,
  'nodeId': '',
  'normalization': '',
  'observationKind': 'multi-instance',
  'property': '',
  'requireIsolated': ['state', 'local-state', 'provisional-state', 'operation-ledger', 'receipts', 'roots'],
  'requireItemControls': [],
  'requireOrderMatch': False,
  'source': 'browser-dom',
  'statePath': ''}]

# These are executable projection mechanics. Their hashes are checked against
# the semantic compatibility identities carried by canonical Workbench IR.
CALLBACK_SOURCES = {('invariant', 'contract-workbench.invariant.contract-keys-unique', 'check'): '(state) => {\n'
                                                                              '        const ids = (state?.contracts '
                                                                              '|| []).map((contract) => contract.id);\n'
                                                                              '        return ids.length === new '
                                                                              'Set(ids).size;\n'
                                                                              '      }',
 ('invariant', 'contract-workbench.invariant.contract-values-valid', 'check'): '(state) => (state?.contracts || '
                                                                               '[]).every((contract) => (\n'
                                                                               '        typeof contract?.id === '
                                                                               '"string"\n'
                                                                               '        && contract.id.length > 0\n'
                                                                               '        && typeof contract.name === '
                                                                               '"string"\n'
                                                                               '        && contract.name.length > 0\n'
                                                                               '        && ["materials", "services", '
                                                                               '"transport"].includes(contract.category)\n'
                                                                               '        && '
                                                                               'Number.isInteger(contract.quantity)\n'
                                                                               '        && contract.quantity > 0\n'
                                                                               '        && ["idle", "running", '
                                                                               '"quoted", "partial", "failed", '
                                                                               '"cancelled"].includes(contract.quoteStatus)\n'
                                                                               '        && '
                                                                               'Number.isInteger(contract.quoteAmount)\n'
                                                                               '        && contract.quoteAmount >= 0\n'
                                                                               '      ))',
 ('invariant', 'contract-workbench.invariant.contracts-array', 'check'): '(state) => Array.isArray(state?.contracts)',
 ('invariant', 'contract-workbench.invariant.revision-nonnegative', 'check'): '(state) => '
                                                                              'Number.isInteger(state?.revision) && '
                                                                              'state.revision >= 0',
 ('operation', 'add-contract', 'ensures'): 'function ensures({before, after}) {\n'
                                           '        return after.contracts.length === before.contracts.length + 1\n'
                                           '          && after.revision === before.revision + 1;\n'
                                           '      }',
 ('operation', 'add-contract', 'preflight'): 'function preflight({payload}) {\n'
                                             '        if (!payload.name) return {ok: false, code: '
                                             '"CONTRACT_NAME_REQUIRED", message: "A contract name is required."};\n'
                                             '        if (!Number.isInteger(payload.quantity) || payload.quantity < 1) '
                                             '{\n'
                                             '          return {ok: false, code: "CONTRACT_QUANTITY_INVALID", message: '
                                             '"Quantity must be a positive integer."};\n'
                                             '        }\n'
                                             '        return {ok: true};\n'
                                             '      }',
 ('operation', 'add-contract', 'transition'): 'function transition({state, payload}) {\n'
                                              '        const contract = {\n'
                                              '          id: `contract-${state.nextContractId}`,\n'
                                              '          name: payload.name,\n'
                                              '          category: payload.category,\n'
                                              '          quantity: payload.quantity,\n'
                                              '          quoteStatus: "idle",\n'
                                              '          quoteAmount: 0\n'
                                              '        };\n'
                                              '        return {\n'
                                              '          contracts: [...state.contracts, contract],\n'
                                              '          nextContractId: state.nextContractId + 1,\n'
                                              '          revision: state.revision + 1\n'
                                              '        };\n'
                                              '      }',
 ('operation', 'clear-all', 'ensures'): 'function ensures({after}) {\n'
                                        '        return after.contracts.length === 0;\n'
                                        '      }',
 ('operation', 'clear-all', 'transition'): 'function transition({state}) {\n'
                                           '        return {contracts: [], revision: state.revision + 1};\n'
                                           '      }',
 ('operation', 'remove-contract', 'ensures'): 'function ensures({before, after, payload}) {\n'
                                              '        return !after.contracts.some((contract) => contract.id === '
                                              'payload.contractId)\n'
                                              '          && after.contracts.length === before.contracts.length - 1\n'
                                              '          && after.revision === before.revision + 1;\n'
                                              '      }',
 ('operation', 'remove-contract', 'preflight'): 'function preflight({state, payload}) {\n'
                                                '        return state.contracts.some((contract) => contract.id === '
                                                'payload.contractId)\n'
                                                '          ? {ok: true}\n'
                                                '          : {ok: false, code: "CONTRACT_NOT_FOUND"};\n'
                                                '      }',
 ('operation', 'remove-contract', 'transition'): 'function transition({state, payload}) {\n'
                                                 '        return {\n'
                                                 '          contracts: state.contracts.filter((contract) => '
                                                 'contract.id !== payload.contractId),\n'
                                                 '          revision: state.revision + 1\n'
                                                 '        };\n'
                                                 '      }',
 ('operation', 'request-quote', 'commit'): 'function commit({state, provisional, payload}) {\n'
                                           '        const progress = provisional[payload.contractId] || {reports: [], '
                                           'failures: []};\n'
                                           '        const amounts = progress.reports.map((report) => '
                                           'Number(report.amount || 0));\n'
                                           '        const amount = amounts.length ? Math.round(amounts.reduce((sum, '
                                           'value) => sum + value, 0) / amounts.length) : 0;\n'
                                           '        const quoteStatus = progress.failures.length ? "partial" : '
                                           '"quoted";\n'
                                           '        return {\n'
                                           '          contracts: state.contracts.map((contract) => (\n'
                                           '            contract.id === payload.contractId\n'
                                           '              ? {...contract, quoteStatus, quoteAmount: amount}\n'
                                           '              : contract\n'
                                           '          )),\n'
                                           '          revision: state.revision + 1\n'
                                           '        };\n'
                                           '      }',
 ('operation', 'request-quote', 'ensures'): 'function ensures({after, payload}) {\n'
                                            '        return after.contracts.some((contract) => (\n'
                                            '          contract.id === payload.contractId\n'
                                            '          && ["quoted", "partial"].includes(contract.quoteStatus)\n'
                                            '        ));\n'
                                            '      }',
 ('operation', 'request-quote', 'receive'): 'function receive({provisional, event, payload}) {\n'
                                            '        const current = provisional[payload.contractId] || {\n'
                                            '          status: "running",\n'
                                            '          received: 0,\n'
                                            '          expected: 0,\n'
                                            '          reports: [],\n'
                                            '          failures: []\n'
                                            '        };\n'
                                            '        const next = {...current, status: "running"};\n'
                                            '        if (event.type === "quote.started") next.expected = '
                                            'event.expected || 0;\n'
                                            '        if (event.type === "quote.received") {\n'
                                            '          next.received += 1;\n'
                                            '          next.reports = [...next.reports, event.report];\n'
                                            '        }\n'
                                            '        if (event.type === "quote.failed") next.failures = '
                                            '[...next.failures, event];\n'
                                            '        return {...provisional, [payload.contractId]: next};\n'
                                            '      }',
 ('operation', 'request-quote', 'run'): 'async function* run({state, payload, capabilities, signal}) {\n'
                                        '        const contract = state.contracts.find((entry) => entry.id === '
                                        'payload.contractId);\n'
                                        '        if (!contract) throw Object.assign(new Error("Contract not found."), '
                                        '{code: "CONTRACT_NOT_FOUND"});\n'
                                        '        yield* capabilities.quotes.requestQuote({\n'
                                        '          contractId: contract.id,\n'
                                        '          category: contract.category,\n'
                                        '          quantity: contract.quantity\n'
                                        '        }, {signal});\n'
                                        '      }',
 ('operation', 'update-quantity', 'ensures'): 'function ensures({after, payload}) {\n'
                                              '        return after.contracts.some((contract) => (\n'
                                              '          contract.id === payload.contractId && contract.quantity === '
                                              'payload.quantity\n'
                                              '        ));\n'
                                              '      }',
 ('operation', 'update-quantity', 'preflight'): 'function preflight({payload}) {\n'
                                                '        return Number.isInteger(payload.quantity) && payload.quantity '
                                                '> 0\n'
                                                '          ? {ok: true}\n'
                                                '          : {ok: false, code: "CONTRACT_QUANTITY_INVALID"};\n'
                                                '      }',
 ('operation', 'update-quantity', 'transition'): 'function transition({state, payload}) {\n'
                                                 '        return {\n'
                                                 '          contracts: state.contracts.map((contract) => (\n'
                                                 '            contract.id === payload.contractId\n'
                                                 '              ? {...contract, quantity: payload.quantity}\n'
                                                 '              : contract\n'
                                                 '          )),\n'
                                                 '          revision: state.revision + 1\n'
                                                 '        };\n'
                                                 '      }',
 ('state', 'canSubmit', 'compute'): '({draftName, draftQuantity}) => (\n'
                                    '        String(draftName || "").trim().length > 0\n'
                                    '        && Number.isInteger(Number(draftQuantity))\n'
                                    '        && Number(draftQuantity) > 0\n'
                                    '      )',
 ('state', 'totalQuantity', 'compute'): '({contracts}) => contracts.reduce((total, contract) => total + '
                                        'contract.quantity, 0)',
 ('state', 'visibleContracts', 'compute'): '({contracts, filterText, sortMode}) => {\n'
                                           '        const normalizedFilter = String(filterText || '
                                           '"").trim().toLowerCase();\n'
                                           '        const filtered = contracts.filter((contract) => (\n'
                                           '          !normalizedFilter\n'
                                           '          || contract.name.toLowerCase().includes(normalizedFilter)\n'
                                           '          || contract.category.toLowerCase().includes(normalizedFilter)\n'
                                           '        ));\n'
                                           '        return [...filtered].sort((left, right) => {\n'
                                           '          if (sortMode === "quantity") return left.quantity - '
                                           'right.quantity || left.id.localeCompare(right.id);\n'
                                           '          return '
                                           'String(left[sortMode]).localeCompare(String(right[sortMode])) || '
                                           'left.id.localeCompare(right.id);\n'
                                           '        });\n'
                                           '      }'}


class WorkbenchProjectionProfileError(ValueError):
    """Raised when canonical IR cannot be projected by this versioned profile."""


@dataclass(frozen=True)
class WorkbenchProjection:
    profile: Mapping[str, Any]
    files: Mapping[str, bytes]
    definition_fingerprint: str


def project_workbench_ir(application_ir: Mapping[str, Any]) -> WorkbenchProjection:
    """Project canonical Workbench IR into deterministic logical package files."""

    ir = dict(application_ir)
    application = _mapping(ir.get("application"))
    fingerprints = _mapping(ir.get("fingerprints"))
    migration = _mapping(ir.get("migration"))

    if application.get("appId") != APP_ID:
        raise WorkbenchProjectionProfileError("Workbench projection received a different application identity.")
    if fingerprints.get("semantic") != EXPECTED_SEMANTIC_FINGERPRINT:
        raise WorkbenchProjectionProfileError("Workbench semantic fingerprint is not the v1 projection authority.")
    if migration.get("expressionProfile") != "mcel.workbench.constrained-expression-profile.v1":
        raise WorkbenchProjectionProfileError("Workbench constrained-expression profile is missing or stale.")
    if migration.get("portableIrProjectionComplete") is not True:
        raise WorkbenchProjectionProfileError("Workbench IR does not declare complete portable projection.")
    if count_native_calls(ir) != EXPECTED_OPERATOR_COUNT or count_opaque_callbacks(ir) != 0:
        raise WorkbenchProjectionProfileError("Workbench IR must contain 26 native calls and zero opaque callbacks.")

    registry = DomainOperatorRegistry.from_records(operator_records()).to_record()
    operators = registry.get("operators") or []
    if len(operators) != EXPECTED_OPERATOR_COUNT:
        raise WorkbenchProjectionProfileError("Workbench operator count is stale.")
    if registry.get("fingerprint") != EXPECTED_OPERATOR_REGISTRY_FINGERPRINT:
        raise WorkbenchProjectionProfileError("Workbench operator registry fingerprint is stale.")

    definition = _definition_from_ir(ir)
    definition_fingerprint, files = render_application_definition_files(
        APP_ID,
        definition,
        source_reference=SOURCE_REFERENCE,
        source_sha256=LEGACY_DEFINITION_SOURCE_SHA256,
    )
    if definition_fingerprint != EXPECTED_DEFINITION_FINGERPRINT:
        raise WorkbenchProjectionProfileError("Workbench compatibility definition fingerprint changed.")
    if set(files) != set(GENERATED_PATHS):
        raise WorkbenchProjectionProfileError("Workbench generated file set is incomplete or unexpected.")

    _verify_roundtrip(ir, definition)
    file_records = [
        {
            "path": relative,
            "sha256": _sha(files[relative]),
        }
        for relative in sorted(files)
    ]
    profile = {
        "schema": PROFILE_SCHEMA,
        "id": PROFILE_ID,
        "appId": APP_ID,
        "expressionProfile": "mcel.workbench.constrained-expression-profile.v1",
        "portableIrProjectionComplete": True,
        "semanticFingerprint": EXPECTED_SEMANTIC_FINGERPRINT,
        "definitionFingerprint": definition_fingerprint,
        "operatorCount": EXPECTED_OPERATOR_COUNT,
        "operatorRegistryFingerprint": EXPECTED_OPERATOR_REGISTRY_FINGERPRINT,
        "files": file_records,
        "materialization": "in-memory",
    }
    return WorkbenchProjection(profile=profile, files=dict(files), definition_fingerprint=definition_fingerprint)


def _definition_from_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    application = _mapping(ir.get("application"))
    states: dict[str, Any] = {}
    derivations = {
        _strip_ref(_mapping(value).get("target"), "state:"): _mapping(value)
        for value in _sequence(ir.get("derivations"))
    }
    for raw in _sequence(ir.get("states")):
        state = _mapping(raw)
        name = str(state.get("sourceName") or "")
        authority = str(state.get("authority") or "")
        entry: dict[str, Any] = {
            "authority": authority,
            "description": str(state.get("description") or ""),
            "kind": "state",
            "schema": _schema_to_definition(_mapping(state.get("schema"))),
        }
        if authority == "derived":
            derivation = derivations.get(name)
            if not derivation:
                raise WorkbenchProjectionProfileError(f"Derived state {name!r} has no canonical derivation.")
            entry["compute"] = _function_record(
                ("state", name, "compute"),
                _legacy_hash(derivation.get("derive")),
            )
            entry["reads"] = _collect_state_reads(derivation.get("derive"))
        else:
            entry["initial"] = copy.deepcopy(state.get("initial"))
        states[name] = entry

    invariant_records = {
        _invariant_source_id(str(_mapping(value).get("id") or "")): _mapping(value)
        for value in _sequence(_mapping(ir.get("proof")).get("invariants"))
    }
    invariants = []
    for invariant_id in INVARIANT_ORDER:
        invariant = invariant_records.get(invariant_id)
        if not invariant:
            raise WorkbenchProjectionProfileError(f"Missing Workbench invariant {invariant_id!r}.")
        invariants.append(
            {
                "check": _function_record(
                    ("invariant", invariant_id, "check"),
                    _legacy_hash(invariant.get("check")),
                ),
                "description": str(invariant.get("description") or ""),
                "id": invariant_id,
                "kind": "invariant",
                "reads": [_strip_ref(value, "state:") for value in _sequence(invariant.get("reads"))],
            }
        )

    capabilities: dict[str, Any] = {}
    for raw in _sequence(ir.get("capabilities")):
        capability = _mapping(raw)
        alias = str(capability.get("sourceName") or "")
        operations = {}
        for operation_raw in _sequence(capability.get("operations")):
            operation = _mapping(operation_raw)
            operations[str(operation.get("sourceName") or "")] = {
                "cancellable": operation.get("cancellable") is True,
                "request": _schema_to_definition(_mapping(operation.get("requestSchema"))),
                "response": _schema_to_definition(_mapping(operation.get("responseSchema"))),
                "stream": operation.get("stream") is True,
            }
        capabilities[alias] = {
            "description": str(capability.get("description") or ""),
            "id": str(capability.get("id") or "").removeprefix("capability:"),
            "kind": "capability",
            "operations": operations,
            "risk": str(capability.get("risk") or ""),
        }

    effects_by_owner: dict[str, list[Mapping[str, Any]]] = {}
    for raw in _sequence(ir.get("effects")):
        effect = _mapping(raw)
        owner = _strip_ref(effect.get("owner"), "intent:")
        effects_by_owner.setdefault(owner, []).append(effect)

    operations: dict[str, Any] = {}
    for raw in _sequence(ir.get("intents")):
        intent = _mapping(raw)
        name = str(intent.get("sourceName") or "")
        payload = {
            str(_mapping(value).get("sourceName") or ""): copy.deepcopy(_mapping(value).get("sourceBinding") or {})
            for value in _sequence(intent.get("input"))
        }
        uses = []
        for effect in effects_by_owner.get(name, []):
            if effect.get("effectKind") != "capability-request":
                continue
            target = _mapping(effect.get("target"))
            if target.get("kind") == "constant":
                uses.append(str(target.get("value") or ""))

        writes = [_strip_ref(value, "state:") for value in _sequence(intent.get("writes"))]
        if intent.get("operationKind") == "cancel":
            writes = [_strip_ref(value, "state:") for value in _sequence(intent.get("provisionalWrites"))]

        operation: dict[str, Any] = {
            "cancel": {"$undefined": True},
            "cancellable": intent.get("cancellable") is True,
            "cancels": _strip_ref(intent.get("cancels"), "intent:") if intent.get("cancels") else "",
            "commit": {"$undefined": True},
            "concurrency": str(intent.get("concurrency") or "serial-per-application"),
            "ensures": {"$undefined": True},
            "id": name,
            "kind": "operation",
            "operationKind": str(intent.get("operationKind") or ""),
            "payload": payload,
            "preflight": {"$undefined": True},
            "provisionalPath": PROVISIONAL_PATHS.get(name, ""),
            "reads": [_strip_ref(value, "state:") for value in _sequence(intent.get("reads"))],
            "reason": str(intent.get("reason") or ""),
            "receive": {"$undefined": True},
            "risk": str(intent.get("risk") or ""),
            "run": {"$undefined": True},
            "transition": {"$undefined": True},
            "uses": sorted(uses),
            "writes": writes,
        }
        if name in PREFLIGHT_HASHES:
            operation["preflight"] = _function_record(
                ("operation", name, "preflight"),
                PREFLIGHT_HASHES[name],
            )
        if intent.get("ensures"):
            operation["ensures"] = _function_record(
                ("operation", name, "ensures"),
                _legacy_hash(intent.get("ensures")),
            )
        if intent.get("operationKind") == "async":
            operation["run"] = _function_record(
                ("operation", name, "run"),
                _legacy_hash(intent.get("request")),
            )
            operation["receive"] = _function_record(
                ("operation", name, "receive"),
                _legacy_hash(intent.get("reconcile")),
            )
            operation["commit"] = _function_record(
                ("operation", name, "commit"),
                _legacy_hash(intent.get("commit")),
            )
        elif intent.get("transition"):
            operation["transition"] = _function_record(
                ("operation", name, "transition"),
                _legacy_hash(intent.get("transition")),
            )
        operations[name] = operation

    surface_ir = _mapping(_sequence(ir.get("surfaces"))[0] if _sequence(ir.get("surfaces")) else None)
    node_records: dict[str, Any] = {}
    for raw in _sequence(surface_ir.get("nodes")):
        node = _mapping(raw)
        node_id = str(node.get("sourceName") or "")
        entry: dict[str, Any] = {
            "id": node_id,
            "nodeKind": str(node.get("nodeKind") or ""),
            "regionId": str(node.get("regionId") or ""),
        }
        if node.get("intent"):
            entry["intentId"] = _strip_ref(node.get("intent"), "intent:")
        if "statePath" in node:
            entry["statePath"] = node.get("statePath")
        for key in (
            "property",
            "transform",
            "inputType",
            "localPath",
            "templateId",
            "keyPath",
            "payload",
            "properties",
            "when",
            "content",
            "accessibility",
            "item",
        ):
            if key in node:
                entry[key] = copy.deepcopy(node[key])
        entry["source"] = copy.deepcopy(SURFACE_NODE_SOURCE_POLICY.get(node_id))
        node_records[node_id] = entry

    defaults = {
        "accessibility": {},
        "content": {},
        "inputType": "",
        "intentId": "",
        "item": {},
        "keyPath": "",
        "kind": "surface-node",
        "localPath": "",
        "payload": {},
        "properties": [],
        "property": "",
        "source": None,
        "statePath": "",
        "templateId": "",
        "transform": "",
        "when": {},
    }
    nodes = []
    for node_id in SURFACE_NODE_ORDER:
        if node_id not in node_records:
            raise WorkbenchProjectionProfileError(f"Missing Workbench surface node {node_id!r}.")
        entry = copy.deepcopy(defaults)
        entry.update(node_records[node_id])
        nodes.append(entry)

    scenarios = {
        str(_mapping(value).get("sourceName") or ""): _mapping(value)
        for value in _sequence(ir.get("scenarios"))
    }
    acceptance = []
    for scenario_id in ACCEPTANCE_ORDER:
        scenario = scenarios.get(scenario_id)
        if not scenario:
            raise WorkbenchProjectionProfileError(f"Missing Workbench scenario {scenario_id!r}.")
        operation_id = "" if scenario.get("crossCutting") else _strip_ref(scenario.get("intent"), "intent:")
        acceptance.append(
            {
                "acceptanceKind": str(scenario.get("acceptanceKind") or ""),
                "expect": copy.deepcopy(scenario.get("expect") or {}),
                "given": copy.deepcopy(scenario.get("given") or {}),
                "id": scenario_id,
                "kind": "acceptance",
                "operationId": operation_id,
                "when": copy.deepcopy(scenario.get("when") or {}),
            }
        )

    proof = _mapping(ir.get("proof"))
    return {
        "acceptance": acceptance,
        "capabilities": capabilities,
        "id": APP_ID,
        "invariants": invariants,
        "layout": copy.deepcopy(_mapping(_sequence(ir.get("layouts"))[0]).get("grammar") or {}),
        "multiInstance": copy.deepcopy(MULTI_INSTANCE_POLICY),
        "observations": copy.deepcopy(OBSERVATIONS_POLICY),
        "operations": operations,
        "proof": {
            "acceptanceStatus": proof.get("acceptanceStatus"),
            "browserObservation": proof.get("browserObservation"),
            "runtimeStatus": proof.get("targetTruthStatus"),
        },
        "requiredRuntimeFeatures": copy.deepcopy(REQUIRED_RUNTIME_FEATURES),
        "schema": "mcel.application-definition.v1",
        "state": states,
        "surface": {
            "id": str(surface_ir.get("sourceName") or ""),
            "kind": "surface",
            "nodes": nodes,
            "regions": copy.deepcopy(surface_ir.get("regions") or []),
            "root": copy.deepcopy(surface_ir.get("root")),
        },
        "title": str(application.get("title") or ""),
    }


def _verify_roundtrip(ir: Mapping[str, Any], definition: Mapping[str, Any]) -> None:
    application = _mapping(ir.get("application"))
    provenance = _mapping(ir.get("provenance"))
    frontend = _mapping(provenance.get("frontend"))
    candidate = definition_to_application_ir(
        definition,
        app_id=APP_ID,
        source=_mapping(application.get("source")),
        source_files=tuple(_mapping(value) for value in _sequence(frontend.get("sourceFiles"))),
        definition_fingerprint=EXPECTED_DEFINITION_FINGERPRINT,
        normalized_reference=NORMALIZED_REFERENCE,
    )
    validation = validate_application_ir(candidate)
    if not validation.valid or validation.normalized is None:
        raise WorkbenchProjectionProfileError("Projected Workbench definition does not validate back to Application IR.")
    if compare_application_ir(ir, validation.normalized).get("status") != "exact":
        raise WorkbenchProjectionProfileError("Projected Workbench definition does not round-trip exactly.")


def _schema_to_definition(schema: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(schema.get("kind") or "unknown")
    if kind == "boolean":
        return {"kind": "schema", "name": "boolean", "describe": {}}
    if kind == "string":
        return {
            "kind": "schema",
            "name": "string",
            "describe": {"maxLength": None, "minLength": schema.get("minLength", 0)},
        }
    if kind in {"integer", "number"}:
        return {
            "kind": "schema",
            "name": kind,
            "describe": {"maximum": None, "minimum": schema.get("minimum")},
        }
    if kind in {"list", "array"}:
        items = _mapping(schema.get("items"))
        return {
            "kind": "schema",
            "name": "array",
            "describe": {"item": str(items.get("kind") or "unknown")},
        }
    if kind in {"record", "object"}:
        return {
            "kind": "schema",
            "name": "object",
            "describe": {"fields": list(schema.get("fields") or [])},
        }
    if kind == "one-of":
        return {
            "kind": "schema",
            "name": "one-of",
            "describe": {"allowed": list(schema.get("enum") or [])},
        }
    return {"kind": "schema", "name": kind, "describe": {}}


def _function_record(path: tuple[str, str, str], expected_hash: Any) -> dict[str, Any]:
    fingerprint = str(expected_hash or "")
    source = CALLBACK_SOURCES.get(path)
    if not fingerprint or source is None:
        raise WorkbenchProjectionProfileError(f"Missing callback projection policy for {path!r}.")
    observed = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
    if observed != fingerprint:
        raise WorkbenchProjectionProfileError(f"Callback projection policy hash mismatch for {path!r}.")
    return {"$function": source, "sha256": fingerprint.removeprefix("sha256:")}


def _legacy_hash(value: Any) -> str | None:
    expression = _mapping(value)
    if expression.get("kind") == "transition.sequence":
        raw = expression.get("implementationHash")
        return str(raw) if raw else None
    compatibility = _mapping(expression.get("compatibility"))
    legacy = _mapping(compatibility.get("legacyOpaqueFunction"))
    raw = legacy.get("functionHash")
    return str(raw) if raw else None


def _collect_state_reads(value: Any) -> list[str]:
    result: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            if node.get("kind") == "state.read":
                name = _strip_ref(node.get("state"), "state:")
                if name and name not in result:
                    result.append(name)
            for key in sorted(node, key=str):
                if str(key) != "compatibility":
                    visit(node[key])
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for child in node:
                visit(child)

    visit(value)
    return result


def _invariant_source_id(ir_id: str) -> str:
    body = ir_id.removeprefix("invariant:")
    if body.startswith("contract-workbench."):
        return "contract-workbench.invariant." + body.removeprefix("contract-workbench.")
    return body


def _strip_ref(value: Any, prefix: str) -> str:
    return str(_mapping(value).get("ref") or "").removeprefix(prefix)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()
