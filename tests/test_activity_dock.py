from __future__ import annotations

import json
import subprocess
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.activity import ActivityBus
from main_computer.docker_executor import DockerInstancePool
from main_computer.rag_assisted_thinking import run_docker_verification
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer


class ActivityBusTests(unittest.TestCase):
    def test_activity_bus_classifies_faults_and_filters(self) -> None:
        bus = ActivityBus(Path.cwd())
        bus.record_signal("vlc-visible-window-warning", {"pid": 123, "message": "window detected"})
        bus.record_signal("api-activity-snapshot", {})

        faults = bus.events(filter_id="faults")
        self.assertTrue(any(event["severity"] == "warn" for event in faults))
        self.assertTrue(any("vlc" in event["tags"] for event in faults))

        bus.record(
            source="rag",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="RAG retrieval completed",
            message="2 chunks selected",
            tags=["rag", "retrieval", "thinking", "local-ai"],
            data={"run_id": "rag_test", "step": "retrieval"},
        )
        rag_events = bus.events(filter_id="rag")
        self.assertTrue(any(event["data"].get("run_id") == "rag_test" for event in rag_events))
        self.assertTrue(any(record["id"] == "rag" for record in bus.snapshot()["filters"]))

        registered = bus.register_filter({"id": "vlc-watch", "label": "VLC Watch", "match": {"tags": ["vlc"]}})
        self.assertEqual(registered["id"], "vlc-watch")
        self.assertTrue(bus.events(filter_id="vlc-watch"))


    def test_docker_instance_pool_records_request_acquire_and_release(self) -> None:
        bus = ActivityBus(Path.cwd())
        pool = DockerInstancePool(size=1, pool_id="test-docker-pool")

        lease = pool.request(
            run_id="run-docker-pool",
            image="python:3.12-slim",
            command_preview="python -V",
            label="before",
            activity_bus=bus,
        )
        self.assertEqual(lease.slot, 1)
        self.assertEqual(pool.status()["free"], 0)

        pool.release(lease, activity_bus=bus, status="completed", returncode=0)

        events = bus.events(limit=20)
        self.assertTrue(any(event["title"] == "Docker pool instance requested" for event in events))
        self.assertTrue(any(event["title"] == "Docker pool instance acquired" for event in events))
        self.assertTrue(any(event["title"] == "Docker pool instance released" for event in events))
        self.assertEqual(pool.status()["free"], 1)

    def test_docker_verification_uses_pool_activity(self) -> None:
        bus = ActivityBus(Path.cwd())

        def fake_run(command, *args, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

        with patch("main_computer.rag_assisted_thinking.subprocess.run", side_effect=fake_run):
            result = run_docker_verification(
                repo_dir=Path.cwd(),
                run_id="run-docker-verification",
                activity_bus=bus,
                command="python -V",
                label="before",
                timeout_s=1,
            )

        self.assertTrue(result.ok)
        serialized = json.dumps(bus.events(limit=80))
        self.assertIn("Docker pool instance acquired", serialized)
        self.assertIn("Docker pool instance released", serialized)
        self.assertIn("docker_executor", serialized)
        self.assertIn("running_text", serialized)


class MachineActivityDockTests(unittest.TestCase):
    def test_applications_page_contains_machine_activity_dock(self) -> None:
        self.assertIn('id="machine-activity-dock"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="machine-activity-heartbeat"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="machine-activity-events"', APPLICATIONS_INDEX_HTML)
        self.assertIn('id="machine-activity-meta"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-activity-filter="faults"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-activity-filter="ai"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-activity-filter="rag"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-activity-filter="thinking"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-activity-filter="docker"', APPLICATIONS_INDEX_HTML)
        self.assertIn('data-activity-open="subprocesses"', APPLICATIONS_INDEX_HTML)
        self.assertNotIn('data-app="git-tools" data-activity-open', APPLICATIONS_INDEX_HTML)
        self.assertIn("window.MainComputerActivityDock", APPLICATIONS_INDEX_HTML)
        self.assertIn("body.activity-dock-collapsed main", APPLICATIONS_INDEX_HTML)
        self.assertIn('body[data-active-app="document"].activity-dock-open main', APPLICATIONS_INDEX_HTML)
        self.assertIn('body[data-active-app="document"].activity-dock-open .machine-activity-dock', APPLICATIONS_INDEX_HTML)
        self.assertIn("grid-template-columns: minmax(420px, 1fr) minmax(64px, 78px);", APPLICATIONS_INDEX_HTML)
        self.assertIn("position: fixed;", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/activity/snapshot", APPLICATIONS_INDEX_HTML)
        self.assertIn("registerFilter", APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-session-thought-history", APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-session-history-panel", APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-session-thought-panel", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("machine-activity-thinking-frame", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("machine-activity-ollama-models-card", APPLICATIONS_INDEX_HTML)
        self.assertIn("/api/activity/ollama-ps", APPLICATIONS_INDEX_HTML)
        self.assertIn("Preserved session history", APPLICATIONS_INDEX_HTML)
        self.assertIn("archiveAiSession", APPLICATIONS_INDEX_HTML)
        self.assertIn("mergeActivityEvents", APPLICATIONS_INDEX_HTML)
        self.assertIn("activityState.backendEvents = mergeActivityEvents(", APPLICATIONS_INDEX_HTML)
        self.assertNotIn(
            "activityState.backendEvents = (snapshot.events || []).map((event) => normalizeEvent(event)).slice(0, ACTIVITY_MAX_EVENTS);",
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn("system_prompt_preview", APPLICATIONS_INDEX_HTML)
        self.assertIn("model stream", APPLICATIONS_INDEX_HTML)
        self.assertIn("RAG types:", APPLICATIONS_INDEX_HTML)
        self.assertIn('active ? (session.streaming ? "streaming" : "alive")', APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-alive", APPLICATIONS_INDEX_HTML)

    def test_activity_dock_merges_backend_snapshots_and_leaves_thinking_to_chat(self) -> None:
        self.assertIn("function mergeActivityEvents(existingEvents = [], incomingEvents = [], maxEvents = ACTIVITY_MAX_EVENTS)", APPLICATIONS_INDEX_HTML)
        self.assertIn("byId.set(normalized.id, previous ? newest(previous, normalized) : normalized)", APPLICATIONS_INDEX_HTML)
        self.assertIn('target.className = `machine-activity-ai-session ${session || archivedTopSession ? "has-session" : "idle"}`', APPLICATIONS_INDEX_HTML)
        self.assertIn("const archivedSessions = visibleArchivedSessions(displayedRunId)", APPLICATIONS_INDEX_HTML)
        self.assertIn("The Chat Console owns the per-node Thinking tab", APPLICATIONS_INDEX_HTML)
        self.assertNotIn('thinkingFrame.className = "machine-activity-thinking-frame";', APPLICATIONS_INDEX_HTML)
        self.assertNotIn("thinkingBody.appendChild(renderOllamaModelsCard());", APPLICATIONS_INDEX_HTML)

    def test_activity_dock_keeps_history_but_not_chat_thinking_frame(self) -> None:
        self.assertIn("Preserved session history", APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-session-history-panel", APPLICATIONS_INDEX_HTML)
        self.assertIn("machine-activity-session-thought-panel", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("renderOllamaModelsCard", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("refreshOllamaModels", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("\"live-snapshot\"", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("\"history-mutated\"", APPLICATIONS_INDEX_HTML)

    def test_activity_routes_snapshot_event_filter_and_meta_model(self) -> None:
        config = MainComputerConfig(model="fake-model", workspace=Path.cwd(), ollama_timeout_s=30)
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)

        class FakeCatalog:
            def project_summaries(self):
                return []

            def list_projects(self):
                return []

        class FakeProvider:
            name = "fake"
            model = "fake-model"

        class FakeComputer:
            catalog = FakeCatalog()
            provider = FakeProvider()

            def chat(self, prompt: str) -> ChatResponse:
                return ChatResponse(content=f"echo: {prompt}", provider="fake", model="fake-model")

        server.computer = FakeComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"

            with urlopen(f"{base}/api/activity/snapshot", timeout=10) as response:
                snapshot = json.loads(response.read().decode("utf-8"))
            self.assertTrue(snapshot["ok"])
            self.assertIn("events", snapshot)
            self.assertIn("heartbeat", snapshot)
            self.assertEqual(snapshot["heartbeat"]["time_model"], "time_series")

            ollama_payload = {
                "models": [
                    {
                        "name": "gemma4:26b",
                        "digest": "02e0f2817a89abcdef",
                        "size": 1610612736,
                        "size_vram": 1610612736,
                        "expires_at": "2026-05-06T00:30:00Z",
                        "details": {"parameter_size": "26B"},
                    },
                    {
                        "model": "gemma4:26b",
                        "digest": "5571076f3d70abcdef",
                        "size": 35433480192,
                        "size_vram": 0,
                        "expires_at": "2026-05-06T00:03:00Z",
                        "details": {"parameter_size": "26B"},
                    },
                ]
            }

            class FakeOllamaResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def read(self):
                    return json.dumps(ollama_payload).encode("utf-8")

            with patch("main_computer.viewport_routes_applications.urlopen", return_value=FakeOllamaResponse()) as mocked_urlopen:
                with urlopen(f"{base}/api/activity/ollama-ps", timeout=10) as response:
                    ollama_result = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{base}/api/activity/ollama-ps", timeout=10) as response:
                    cached_ollama_result = json.loads(response.read().decode("utf-8"))

            self.assertTrue(ollama_result["ok"])
            self.assertEqual(ollama_result["source"], "ollama-api")
            self.assertEqual(mocked_urlopen.call_count, 1)
            self.assertEqual(cached_ollama_result["models"], ollama_result["models"])
            self.assertTrue(cached_ollama_result["cached"])
            self.assertEqual(ollama_result["models"][0]["name"], "gemma4:26b")
            self.assertEqual(ollama_result["models"][0]["processor"], "100% GPU")
            self.assertEqual(ollama_result["models"][1]["name"], "gemma4:26b")
            self.assertEqual(ollama_result["models"][1]["processor"], "CPU")
            self.assertEqual(ollama_result["models"][1]["context"], "26B")

            server.chat_ai_processes.remember_route_result(
                run_id="recover-run",
                payload={
                    "ok": True,
                    "status": "completed",
                    "output_cell": {"id": "out-recover-run", "type": "output", "status": "ok", "parts": []},
                    "thread_id": "recover-thread",
                },
            )
            with urlopen(f"{base}/api/applications/chat-console/ai/run-result?run_id=recover-run", timeout=10) as response:
                run_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(run_result["ok"])
            self.assertTrue(run_result["completed"])
            self.assertEqual(run_result["output_cell"]["id"], "out-recover-run")

            event_request = Request(
                f"{base}/api/activity/event",
                data=json.dumps(
                    {
                        "source": "test",
                        "kind": "subprocess",
                        "time_model": "parallel",
                        "severity": "warn",
                        "title": "Visible window warning",
                        "tags": ["vlc", "fault"],
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(event_request, timeout=10) as response:
                event_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(event_result["ok"])
            self.assertTrue(event_result["event"]["fault"])

            filter_request = Request(
                f"{base}/api/activity/filter",
                data=json.dumps({"id": "vlc-watch", "label": "VLC Watch", "match": {"tags": ["vlc"]}}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(filter_request, timeout=10) as response:
                filter_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(filter_result["ok"])
            self.assertEqual(filter_result["filter"]["id"], "vlc-watch")

            with urlopen(f"{base}/api/activity/events?filter=vlc-watch", timeout=10) as response:
                events_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(events_result["ok"])
            self.assertTrue(any("vlc" in event["tags"] for event in events_result["events"]))

            with urlopen(f"{base}/api/activity/meta-model", timeout=10) as response:
                meta_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(meta_result["ok"])
            self.assertIn("machine.activity.dock", json.dumps(meta_result["meta_model"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
