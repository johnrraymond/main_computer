"""Read-only Patch 11 initial semantic knowledge/observations Frob.

No app server and no operational controls are invoked. The fixture follows
TaskManagerService.snapshot() but does not certify its live response.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.mcel_knowledge import GRAPH_SCHEMA, interpret, load_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/fixtures/mcel_computer_knowledge/task_manager_snapshot.v1.json"


def evaluate() -> dict:
    catalog = load_catalog()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    graph = interpret(data, catalog)
    processes = graph["sources"]["task-manager.processes"]["records"]
    connections = graph["sources"]["task-manager.connections"]["records"]
    cpu = graph["sources"]["task-manager.cpu"]["records"][0]["properties"]
    gpu = graph["sources"]["task-manager.gpu"]["records"][0]["properties"]
    checks = {
        "valid-knowledge-db": len(catalog["concepts"]) >= 26 and sum(k.startswith("task-manager.") for k in catalog["observations"]) == 8,
        "ui-independent-interpretation": graph["schema"] == GRAPH_SCHEMA,
        "source-preserving-memory-absence": processes[0]["properties"]["resident_memory"]["state"] == "unavailable",
        "genuine-zero-is-present": processes[1]["properties"]["resident_memory"]["raw"] == 0 and processes[1]["properties"]["resident_memory"]["state"] == "present",
        "process-generation-identity": processes[0]["identity"]["parts"] == [101, "/Date(1793132654474)/"],
        "socket-owner-uncertainty": connections[0]["relationships"][0]["status"] == "candidate-only",
        "unresolved-process-link": connections[1]["relationships"][0]["status"] == "unresolved",
        "cpu-measured-zero": cpu["utilization"]["raw"] == 0 and cpu["utilization"]["state"] == "present",
        "unmeasured-gpu-not-zero": gpu["utilization"]["state"] == "unavailable",
        "operation-boundary": all(not op["authorized"] and not op["executable"] for op in graph["operations"].values()),
        "no-authority-claims": processes[0]["properties"]["is_main_computer"]["evidence"] == "application-heuristic-not-os-authority",
    }
    return {"schema": "mcel.semantic-knowledge-frob.v1", "status": "PASS" if all(checks.values()) else "FAIL",
            "scope": "offline-semantic-only", "counts": {"concepts": len(catalog["concepts"]),
            "observations": len(catalog["observations"]), "operations": len(catalog["operations"])},
            "checks": checks, "catalogFragments": catalog["fragments"], "hostedValidated": False,
            "guiValidated": False, "effectsExecuted": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=ROOT / "runtime/reports/mcel-semantic-knowledge-first-cut.json")
    args = parser.parse_args()
    report = evaluate()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("MCEL_SEMANTIC_KNOWLEDGE_FIRST_CUT_" + report["status"])
    print("concepts={concepts} observation_families={observations} declared_operations={operations}".format(**report["counts"]))
    for name, passed in report["checks"].items():
        print(("PASS" if passed else "FAIL") + " " + name)
    print("report:", args.report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
