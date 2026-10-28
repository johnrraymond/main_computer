"""Patch 11 first cut: no UI, operations, network, or host calls."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from main_computer.mcel_knowledge import (
    SCHEMA, SemanticCatalogError, explain, interpret, load_catalog,
)


def snapshot():
    return {
        "ok": True, "platform": "win32", "processes": [
            {"pid": 101, "name": "python.exe", "status": "running", "status_availability": "present",
             "cmdline": "python -m main_computer.app_control", "memory_rss": None,
             "memory_availability": "unavailable", "created_at": "/Date(1793132654474)/",
             "is_main_computer": True, "is_current_process": False},
            {"pid": 202, "name": "python.exe", "status": "", "status_availability": "unavailable",
             "cmdline": "python worker.py", "memory_rss": 0,
             "memory_availability": "present", "created_at": "2026-10-27T12:00:00Z",
             "is_main_computer": False, "is_current_process": True},
        ],
        "connections": [
            {"pid": 101, "process_name": "python.exe", "status": "Listen", "local": "[::1]:8765", "remote": ""},
            {"pid": 999, "process_name": "unknown", "status": "Established", "local": "127.0.0.1:2000", "remote": "8.8.8.8:443"},
        ],
        "hardware": {
            "updated_at": "2026-10-27T12:01:00Z",
            "cpu": {"available": True, "overall_percent": 0, "physical_cores": 8,
                    "logical_cores": 16, "frequency_mhz": 3600,
                    "per_core": [{"index": 0, "label": "CPU 0", "percent": 13.5}]},
            "gpu": {"available": False, "overall_percent": None, "devices": []},
        },
        "server": {"running": True, "pid": 101, "port": 8765, "listener": "[::1]:8765",
                   "heartbeat_running": True, "heartbeat_ready": False, "heartbeat_pid": 900,
                   "control_script": "C:\\Main Computer\\control-main-computer.ps1"},
        "schedules": [
            {"id": "s1", "action": "server_restart", "run_at": "2026-10-28T11:30:00Z",
             "created_at": "2026-10-27T12:01:00Z", "enabled": False, "note": "review"}
        ],
    }


class ComputerKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_registry_has_real_task_manager_families(self):
        self.assertEqual(sum(k.startswith("task-manager.") for k in self.catalog["observations"]), 8)
        self.assertGreaterEqual(len(self.catalog["concepts"]), 25)
        self.assertEqual(explain("mc.process.pid")["base"], "mc.identifier")
        self.assertEqual(explain("task-manager.processes")["identity"]["fields"], ["pid", "created_at"])

    def test_value_state_preserves_unmeasured_vs_true_zero(self):
        model = interpret(snapshot(), self.catalog)
        rows = model["sources"]["task-manager.processes"]["records"]
        self.assertEqual(rows[0]["properties"]["resident_memory"]["state"], "unavailable")
        self.assertIsNone(rows[0]["properties"]["resident_memory"]["raw"])
        self.assertEqual(rows[1]["properties"]["resident_memory"]["state"], "present")
        self.assertEqual(rows[1]["properties"]["resident_memory"]["raw"], 0)
        self.assertEqual(rows[0]["properties"]["resident_memory"]["unit"], "bytes")
        self.assertEqual(model["sources"]["task-manager.cpu"]["records"][0]["properties"]["utilization"]["raw"], 0)
        self.assertEqual(model["sources"]["task-manager.gpu"]["records"][0]["properties"]["utilization"]["state"], "unavailable")

    def test_process_lifetime_identity_and_candidate_only_connections(self):
        model = interpret(snapshot(), self.catalog)
        process = model["sources"]["task-manager.processes"]["records"][0]
        self.assertEqual(process["identity"]["parts"], [101, "/Date(1793132654474)/"])
        links = model["sources"]["task-manager.connections"]["records"]
        self.assertEqual(links[0]["relationships"][0]["status"], "candidate-only")
        self.assertEqual(links[0]["relationships"][0]["candidateIndices"], [0])
        self.assertEqual(links[1]["relationships"][0]["status"], "unresolved")

    def test_missing_generation_is_not_invented(self):
        s = snapshot(); s["processes"][0]["created_at"] = ""
        row = interpret(s)["sources"]["task-manager.processes"]["records"][0]
        self.assertEqual(row["identity"]["status"], "incomplete")
        self.assertIsNone(row["identity"]["parts"])

    def test_duplicate_identity_is_marked_ambiguous(self):
        s = snapshot()
        s["processes"].append(deepcopy(s["processes"][0]))
        model = interpret(s)
        rows = model["sources"]["task-manager.processes"]["records"]
        self.assertEqual(rows[0]["identity"]["status"], "ambiguous")
        self.assertEqual(rows[2]["identity"]["status"], "ambiguous")
        self.assertIn("duplicate-identity", [d["code"] for d in model["diagnostics"]])

    def test_invalid_noninteger_core_count(self):
        s = snapshot()
        s["hardware"]["cpu"]["logical_cores"] = 2.5
        value = interpret(s)["sources"]["task-manager.cpu"]["records"][0]["properties"]["logical_cores"]
        self.assertEqual(value["state"], "invalid-value")

    def test_missing_and_false_remain_distinct(self):
        s = snapshot(); del s["processes"][0]["memory_rss"]
        del s["processes"][0]["is_current_process"]
        values = interpret(s)["sources"]["task-manager.processes"]["records"]
        self.assertEqual(values[0]["properties"]["resident_memory"]["state"], "unavailable")
        self.assertEqual(values[0]["properties"]["is_current_process"]["state"], "missing")
        self.assertEqual(values[1]["properties"]["is_current_process"]["state"], "present")
        self.assertIs(values[1]["properties"]["is_current_process"]["raw"], True)

    def test_invalid_measurements_are_never_clamped(self):
        s = snapshot(); s["hardware"]["cpu"]["overall_percent"] = 240
        self.assertEqual(interpret(s)["sources"]["task-manager.cpu"]["records"][0]["properties"]["utilization"]["state"], "invalid-value")
        s["hardware"]["cpu"]["overall_percent"] = None
        self.assertEqual(interpret(s)["sources"]["task-manager.cpu"]["records"][0]["properties"]["utilization"]["state"], "null")
        s["hardware"]["cpu"]["overall_percent"] = False
        self.assertEqual(interpret(s)["sources"]["task-manager.cpu"]["records"][0]["properties"]["utilization"]["state"], "invalid-value")

    def test_invalid_availability_contradiction(self):
        s = snapshot(); s["processes"][1]["memory_rss"] = None
        v = interpret(s)["sources"]["task-manager.processes"]["records"][1]["properties"]["resident_memory"]
        self.assertEqual(v["state"], "contradiction")

    def test_missing_source_not_silent_empty(self):
        s = snapshot(); del s["connections"]
        self.assertEqual(interpret(s)["sources"]["task-manager.connections"]["state"], "missing-source")
        s["connections"] = []
        self.assertEqual(interpret(s)["sources"]["task-manager.connections"]["state"], "present")

    def test_no_snapshot_mutation_and_exact_evidence(self):
        raw = snapshot(); before = deepcopy(raw)
        graph = interpret(raw)
        self.assertEqual(raw, before)
        self.assertEqual(graph["sources"]["task-manager.server"]["records"][0]["properties"]["control_script"]["raw"], "C:\\Main Computer\\control-main-computer.ps1")
        self.assertEqual(graph["sources"]["task-manager.processes"]["records"][0]["properties"]["is_main_computer"]["evidence"], "application-heuristic-not-os-authority")

    def test_operations_never_authorize_or_execute(self):
        graph = interpret(snapshot())
        self.assertEqual(sum(k.startswith("task-manager.") for k in graph["operations"]), 6)
        for x in graph["operations"].values():
            self.assertIs(x["executable"], False)
            self.assertIs(x["authorized"], False)
        self.assertTrue(graph["operations"]["task-manager.terminate"]["confirmationRequired"])

    def test_schema_extensibility_without_interpreter_changes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for f in Path(__file__).resolve().parents[1].joinpath('main_computer/mcel_knowledge/catalog').glob('*.json'):
                (root / f.name).write_bytes(f.read_bytes())
            extension = {"schema": SCHEMA, "namespace": "sample-app",
                         "concepts": [{"id": "sample-app.job", "kind": "entity", "meaning": "A job"}],
                         "observations": [{"id":"sample-app.jobs", "concept":"sample-app.job", "source":"jobs",
                                           "cardinality":"many", "identity":{"fields":["job_id"],"scope":"sample-app","requiresAll":True},
                                           "properties":[{"name":"job_id","source":"job_id","type":"mc.identifier"},
                                                         {"name":"size","source":"size","type":"mc.quantity.bytes"}]}]}
            (root / '20-sample-app.json').write_text(json.dumps(extension))
            cat = load_catalog(root)
            g = interpret({"jobs": [{"job_id": "x", "size": 3}]}, cat)
            self.assertEqual(g["sources"]["sample-app.jobs"]["records"][0]["properties"]["size"]["raw"], 3)
            self.assertEqual(g["sources"]["sample-app.jobs"]["records"][0]["identity"]["status"], "complete")

    def test_catalog_rejects_unsafe_authority_claims_and_unknown_types(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for f in Path(__file__).resolve().parents[1].joinpath('main_computer/mcel_knowledge/catalog').glob('*.json'):
                (root / f.name).write_bytes(f.read_bytes())
            path = root / '10-task-manager.json'
            data = json.loads(path.read_text()); data['operations'][0]['implementedHere'] = True
            path.write_text(json.dumps(data))
            with self.assertRaises(SemanticCatalogError): load_catalog(root)
            data['operations'][0]['implementedHere'] = False
            data['observations'][0]['properties'][0]['type'] = 'undefined-domain-type'
            path.write_text(json.dumps(data))
            with self.assertRaises(SemanticCatalogError): load_catalog(root)

    def test_observation_list_exposes_no_os_actions(self):
        # This module must remain usable even if TaskManagerService isn't instantiated.
        from main_computer import mcel_knowledge
        self.assertFalse(hasattr(mcel_knowledge, 'execute'))
        self.assertFalse(hasattr(mcel_knowledge, 'perform_action'))


if __name__ == '__main__':
    unittest.main()
