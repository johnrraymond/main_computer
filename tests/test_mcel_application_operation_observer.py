from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OBSERVER = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-operation-observer.js"
HOST_HTML = ROOT / "main_computer" / "web" / "mcel-package-host.html"
HOST_SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-application-package-host.js"


def _run_node_json(tmp_path: Path, body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    script = tmp_path / "operation-observer-test.js"
    script.write_text(OBSERVER.read_text(encoding="utf-8") + "\n" + body, encoding="utf-8")
    completed = subprocess.run(
        [node, str(script)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def test_operation_observer_compares_committed_state_receipt_and_fingerprints(tmp_path: Path) -> None:
    data = _run_node_json(
        tmp_path,
        r'''
globalThis.getComputedStyle = () => ({display: "block", visibility: "visible", opacity: "1"});
class Element {
  constructor(nodeId, text = "") { this.nodeId = nodeId; this.textContent = text; this.hidden = false; }
  getAttribute(name) { return name === "data-mcel-node-id" ? this.nodeId : null; }
  getBoundingClientRect() { return {width: 20, height: 20}; }
}
const value = new Element("contract-counter.value", "1");
const receiptValue = {
  operationId: "contract-counter.increment.1",
  intentId: "increment",
  status: "committed",
  after: {revision: 1, state: {count: 1, revision: 1}}
};
const receipt = new Element("contract-counter.latest-receipt", JSON.stringify(receiptValue));
const root = {
  children: [value, receipt],
  getAttribute(name) { return name === "data-mcel-surface-id" ? "contract-counter.surface.primary" : null; },
  querySelectorAll(selector) { return selector === "[data-mcel-node-id]" ? [value, receipt] : []; }
};
const contract = {
  schema: "mcel.observation-contract.v1",
  appId: "contract-counter",
  currentStatus: "operation-linked",
  observations: [
    {id: "value", semanticNodeId: "contract-counter.value", property: "textContent", compareToStatePath: "count", normalization: "string"},
    {id: "visible", semanticNodeId: "contract-counter.value", property: "visible", expected: true, normalization: "boolean"},
    {id: "receipt", semanticNodeId: "contract-counter.latest-receipt", property: "textContent", compareToOperationReceipt: true}
  ]
};
const result = {
  ok: true,
  status: "committed",
  appId: "contract-counter",
  operationId: "contract-counter.increment.1",
  intentId: "increment",
  revision: 1,
  receipt: {before: {revision: 0}, after: {revision: 1}, operationId: "contract-counter.increment.1", status: "committed"}
};
const mount = {
  kind: "mcel-application-package-mount",
  appId: "contract-counter",
  root,
  surface: {surfaceId: "contract-counter.surface.primary"},
  observation: contract,
  packageRecord: {fingerprint: "sha256:package"},
  manifest: {projection: {fingerprint: "sha256:projection"}, source: {catalogFingerprint: "sha256:catalog"}, surface: {rootSelector: "#contract-counter-app"}},
  readState() { return {count: 1, revision: 1}; }
};
const fakeProducer = {captureReadOnlyObservation(input) { return {schema: "mcel.observation-bundle.v1", observationId: input.observationId}; }};
const report = McelApplicationOperationObserver.observeCommittedOperation({
  mount,
  operationResult: result,
  repositoryFingerprint: "repo-fingerprint",
  packageFingerprint: "sha256:package",
  runtimeProjectionFingerprint: "sha256:projection",
  route: "/mcel-package-host.html?app=contract-counter",
  surfaceLocator: "#contract-counter-app"
}, {observationProducer: fakeProducer});
const failures = [];
value.textContent = "9";
try {
  McelApplicationOperationObserver.observeCommittedOperation({mount, operationResult: result, repositoryFingerprint: "repo-fingerprint"}, {observationProducer: fakeProducer});
} catch (error) { failures.push(error.code); }
value.textContent = "1";
receipt.textContent = JSON.stringify({...receiptValue, operationId: "wrong"});
try {
  McelApplicationOperationObserver.observeCommittedOperation({mount, operationResult: result, repositoryFingerprint: "repo-fingerprint"}, {observationProducer: fakeProducer});
} catch (error) { failures.push(error.code); }
try {
  McelApplicationOperationObserver.observeCommittedOperation({mount, operationResult: result, repositoryFingerprint: "repo-fingerprint", packageFingerprint: "sha256:stale"}, {observationProducer: fakeProducer});
} catch (error) { failures.push(error.code); }
process.stdout.write(JSON.stringify({report, failures}));
''',
    )

    assert data["report"]["status"] == "pass"
    assert data["report"]["comparison"] == {
        "stateMatches": True,
        "receiptMatches": True,
        "surfaceMatches": True,
        "checks": data["report"]["comparison"]["checks"],
    }
    assert data["report"]["observedNodes"]["contract-counter.value"]["textContent"] == "1"
    assert data["report"]["receiptObservation"]["operationId"] == "contract-counter.increment.1"
    assert data["failures"] == [
        "MCEL_APPLICATION_OBSERVATION_COMPARISON_FAILED",
        "MCEL_APPLICATION_OBSERVATION_COMPARISON_FAILED",
        "MCEL_APPLICATION_OBSERVATION_PACKAGE_FINGERPRINT_MISMATCH",
    ]


def test_generic_package_host_loads_observation_authorities() -> None:
    html = HOST_HTML.read_text(encoding="utf-8")
    script = HOST_SCRIPT.read_text(encoding="utf-8")

    assert "mcel-application-operation-observer.js" in html
    assert "mcel-browser-observation-producer.js" in html
    assert "mcel-application-package-host.js" in html
    assert "dispatchAndObserve" in script
    assert "McelApplicationPackageHost" in script
    assert "MCEL.applicationPackageMount" not in script  # accessed through the generic authority helper
    assert "applicationPackageMount(root)" in script
