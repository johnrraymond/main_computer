from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.harness import HarnessComputer, WidgetHarness
from main_computer.models import ChatMessage
from main_computer.providers import OllamaProvider
from main_computer.router import MainComputer
from main_computer.viewport import ViewportServer


NORMAL_LEVELS = ("health", "server", "widgets", "live", "functional")
SPECIAL_LEVELS = ("level-1-telemetry", "ollama-probe", "ollama-primer", "ollama-visibility")
LEVELS = (*NORMAL_LEVELS, *SPECIAL_LEVELS)


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    ok: bool
    level: str
    detail: Any = None


class DiagnosticFailure(RuntimeError):
    def __init__(self, message: str, *, report: dict[str, Any] | None = None, report_path: Path | None = None) -> None:
        super().__init__(message)
        self.report = report
        self.report_path = report_path


class DiagnosticRunner:
    def __init__(
        self,
        *,
        config: MainComputerConfig,
        level: str = "widgets",
        output_dir: Path = Path("diagnostics_output"),
        url: str | None = None,
        headed: bool = False,
    ) -> None:
        if level not in LEVELS:
            raise ValueError(f"Unknown diagnostic level: {level}")
        self.config = config
        self.level = level
        self.output_dir = output_dir
        self.url = url
        self.headed = headed
        self.checks: list[DiagnosticCheck] = []

    def run(self, *, raise_on_failure: bool = True) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()

        if self.level == "level-1-telemetry":
            self._run_level1_telemetry()
            return self._finish_report(started, raise_on_failure=raise_on_failure)

        if self.level == "ollama-probe":
            with self._ollama_single_flight_lock():
                self._run_health()
                self._run_ollama_probe()
            return self._finish_report(started, raise_on_failure=raise_on_failure)

        if self.level == "ollama-primer":
            with self._ollama_single_flight_lock():
                self._run_health()
                self._run_ollama_primer()
            return self._finish_report(started, raise_on_failure=raise_on_failure)

        if self.level == "ollama-visibility":
            with self._ollama_single_flight_lock():
                self._run_health()
                self._run_ollama_visibility()
            return self._finish_report(started, raise_on_failure=raise_on_failure)

        self._run_health()
        if self._includes("server"):
            self._run_server()
        if self._includes("widgets"):
            self._run_widgets()
        if self._includes("live"):
            self._run_live_provider()
        if self._includes("functional"):
            self._run_functional_provider()

        return self._finish_report(started, raise_on_failure=raise_on_failure)

    def _finish_report(self, started: float, *, raise_on_failure: bool = True) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        ok = all(check.ok for check in self.checks)
        report = {
            "ok": ok,
            "level": self.level,
            "elapsed_s": round(elapsed, 3),
            "config": {
                "workspace": str(self.config.workspace),
                "provider": self.config.provider,
                "model": self.config.model,
                "patch_level": self.config.patch_level,
                "ollama_base_url": self.config.ollama_base_url,
                "ollama_timeout_s": self.config.ollama_timeout_s,
                "openai_base_url": self.config.openai_base_url,
            },
            "checks": [asdict(check) for check in self.checks],
            "output_dir": str(self.output_dir.resolve()),
        }
        report_path = self.output_dir / "diagnostics_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not ok and raise_on_failure:
            raise DiagnosticFailure(f"Diagnostics failed. Report: {report_path}", report=report, report_path=report_path)
        return report

    def _includes(self, level: str) -> bool:
        if self.level not in NORMAL_LEVELS:
            return False
        return NORMAL_LEVELS.index(self.level) >= NORMAL_LEVELS.index(level)

    def _record(self, name: str, ok: bool, level: str, detail: Any = None) -> None:
        self.checks.append(DiagnosticCheck(name=name, ok=ok, level=level, detail=detail))

    @contextmanager
    def _ollama_single_flight_lock(self) -> Iterator[None]:
        lock_dir = self.config.workspace / ".main_computer_ollama.lock"
        wait_s = max(30.0, min(self.config.ollama_timeout_s, 600.0))
        stale_s = max(900.0, self.config.ollama_timeout_s * 2)
        deadline = time.monotonic() + wait_s

        while True:
            try:
                lock_dir.mkdir()
                (lock_dir / "owner.json").write_text(
                    json.dumps(
                        {
                            "level": self.level,
                            "created_at": time.time(),
                            "output_dir": str(self.output_dir),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self._record(
                    "ollama-single-flight-lock-acquired",
                    True,
                    self.level,
                    {"lock": str(lock_dir), "wait_s": wait_s},
                )
                try:
                    yield
                finally:
                    shutil.rmtree(lock_dir, ignore_errors=True)
                return
            except FileExistsError:
                try:
                    age_s = time.time() - lock_dir.stat().st_mtime
                except OSError:
                    age_s = 0.0
                if age_s > stale_s:
                    shutil.rmtree(lock_dir, ignore_errors=True)
                    continue
                if time.monotonic() >= deadline:
                    self._record(
                        "ollama-single-flight-lock-acquired",
                        False,
                        self.level,
                        {"lock": str(lock_dir), "wait_s": wait_s, "age_s": round(age_s, 3)},
                    )
                    raise TimeoutError(f"Timed out waiting for Ollama diagnostic lock: {lock_dir}")
                time.sleep(0.5)

    def _run_level1_telemetry(self) -> None:
        try:
            from main_computer.level1_telemetry import collect_level1_telemetry

            known_ports = {
                "app": 8765,
                "heartbeat": 8766,
                "hub": 8770,
                "worker": 8771,
                "ollama": int(urlsplit(self.config.ollama_base_url).port or 11434),
                "gitea": 3000,
                "blockchain": int(urlsplit(self.config.energy_chain_rpc_url or "http://127.0.0.1:18545").port or 18545),
            }
            report = collect_level1_telemetry(
                Path.cwd(),
                control_root=Path.cwd(),
                known_ports=known_ports,
            )
            report_path = self.output_dir / "level1_telemetry_report.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            self._record(
                "level-1-telemetry-snapshot",
                bool(report.get("ok")) and int(summary.get("process_count") or 0) > 0,
                "level-1-telemetry",
                {
                    "report": str(report_path),
                    "process_count": summary.get("process_count"),
                    "connection_count": summary.get("connection_count"),
                    "known_port_listener_count": summary.get("known_port_listener_count"),
                    "known_port_time_wait_count": summary.get("known_port_time_wait_count"),
                    "observation_count": summary.get("observation_count"),
                    "operator_summary": report.get("operator_summary", {}),
                    "warnings": report.get("warnings", []),
                },
            )
        except Exception as exc:
            self._record("level-1-telemetry-snapshot", False, "level-1-telemetry", str(exc))

    def _run_health(self) -> None:
        workspace_exists = self.config.workspace.exists() and self.config.workspace.is_dir()
        self._record(
            "workspace-root-exists",
            workspace_exists,
            "health",
            {"workspace": str(self.config.workspace)},
        )
        if not workspace_exists:
            return

        catalog = ProjectCatalog(self.config.workspace)
        projects = catalog.list_projects()
        self._record(
            "catalog-loads-projects",
            len(projects) > 0,
            "health",
            {"project_count": len(projects)},
        )
        self._record(
            "main-computer-project-present",
            any(project.name in {"main_computer", "main_computer_test", "main_copmputer_production"} for project in projects),
            "health",
            {"sample": [project.name for project in projects[:10]]},
        )

        try:
            computer = MainComputer.build(self.config)
            provider_detail = {"provider": computer.provider.name, "model": computer.provider.model}
            self._record("provider-builds", True, "health", provider_detail)
        except Exception as exc:
            self._record("provider-builds", False, "health", str(exc))

    def _run_server(self) -> None:
        if self.url:
            self._check_server_url(self.url.rstrip("/"), level="server")
            return

        server = ViewportServer(("127.0.0.1", 0), self.config, verbose=False)
        server.computer = HarnessComputer()  # type: ignore[assignment]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self._check_server_url(f"http://127.0.0.1:{server.server_port}", level="server")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def _check_server_url(self, base_url: str, *, level: str) -> None:
        text = self._get_text(f"{base_url}/")
        self._record("text-route-serves-console", "Main Computer Console" in text, level, {"url": f"{base_url}/"})

        graphical = self._get_text(f"{base_url}/graphical")
        graphical_expected = (
            "Main Computer Control Panel",
            "/graphical renamed into a useful live machine and services view",
        )
        self._record(
            "graphical-route-serves-widget-test",
            all(marker in graphical for marker in graphical_expected),
            level,
            {"url": f"{base_url}/graphical", "expected": list(graphical_expected)},
        )

        projects = self._get_json(f"{base_url}/api/projects")
        self._record(
            "projects-api-returns-catalog",
            isinstance(projects.get("projects"), list) and "provider" in projects,
            level,
            {"project_count": len(projects.get("projects", [])), "provider": projects.get("provider")},
        )

        request = Request(
            f"{base_url}/api/chat",
            data=json.dumps({"prompt": "diagnostic server ping"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            chat = json.loads(response.read().decode("utf-8"))
        self._record(
            "chat-api-roundtrip",
            "diagnostic server ping" in str(chat.get("content", "")),
            level,
            {"provider": chat.get("provider"), "model": chat.get("model")},
        )

    def _run_widgets(self) -> None:
        harness_dir = self.output_dir / "widget_harness"
        harness = WidgetHarness(
            url=self.url,
            output_dir=harness_dir,
            headless=not self.headed,
        )
        report = harness.run()
        self._record(
            "browser-widget-harness",
            bool(report.get("ok")),
            "widgets",
            {
                "checks": len(report.get("checks", [])),
                "report": str(harness_dir / "widget_harness_report.json"),
            },
        )

    def _run_live_provider(self) -> None:
        try:
            computer = MainComputer.build(self.config)
            response = computer.chat("diagnostic live provider ping")
            ok = bool(response.content.strip())
            detail = {
                "provider": response.provider,
                "model": response.model,
                "content_preview": response.content[:160],
            }
            self._record("live-provider-chat", ok, "live", detail)
        except Exception as exc:
            self._record("live-provider-chat", False, "live", str(exc))

    def _run_functional_provider(self) -> None:
        expected = "Main Computer level five functional diagnostic passed."
        try:
            computer = MainComputer.build(self.config)
            response = computer.chat(f"Reply with exactly this sentence: {expected}")
            ok = expected in response.content
            detail = {
                "provider": response.provider,
                "model": response.model,
                "expected": expected,
                "content_preview": response.content[:240],
            }
            self._record("functional-provider-exact-response", ok, "functional", detail)
        except Exception as exc:
            self._record("functional-provider-exact-response", False, "functional", str(exc))

    def _ollama_diagnostic_provider(self, *, num_predict: int = 64) -> OllamaProvider:
        return OllamaProvider(
            model=self.config.model or "gemma4:26b",
            base_url=self.config.ollama_base_url,
            timeout_s=self.config.ollama_timeout_s,
            options={"temperature": 0, "num_predict": num_predict},
            think=False,
        )

    def _run_ollama_probe(self) -> None:
        try:
            tags = self._get_json(f"{self.config.ollama_base_url.rstrip('/')}/api/tags")
            models = [str(model.get("name", "")) for model in tags.get("models", []) if isinstance(model, dict)]
            expected_model = self.config.model or "gemma4:26b"
            self._record(
                "ollama-probe-tags",
                bool(models),
                "ollama-probe",
                {"model_count": len(models), "models": models[:20], "expected_model": expected_model},
            )
            self._record(
                "ollama-probe-model-listed",
                expected_model in models,
                "ollama-probe",
                {"expected_model": expected_model, "models": models[:20]},
            )
        except Exception as exc:
            self._record("ollama-probe-tags", False, "ollama-probe", str(exc))

        generate_payload = {
            "model": self.config.model or "gemma4:26b",
            "prompt": "Reply with exactly READY.",
            "stream": False,
            "options": {"temperature": 0, "num_predict": 16},
        }
        try:
            data = self._post_json(f"{self.config.ollama_base_url.rstrip('/')}/api/generate", generate_payload)
            content = str(data.get("response", ""))
            self._record(
                "ollama-probe-generate-completes",
                bool(data.get("done")),
                "ollama-probe",
                self._ollama_raw_detail(data, content),
            )
        except Exception as exc:
            self._record("ollama-probe-generate-completes", False, "ollama-probe", str(exc))

        chat_payload = {
            "model": self.config.model or "gemma4:26b",
            "messages": [{"role": "user", "content": "Reply with exactly READY."}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 16},
        }
        try:
            data = self._post_json(f"{self.config.ollama_base_url.rstrip('/')}/api/chat", chat_payload)
            content = str(data.get("message", {}).get("content", ""))
            self._record(
                "ollama-probe-chat-ready",
                bool(content.strip()),
                "ollama-probe",
                self._ollama_raw_detail(data, content),
            )
        except Exception as exc:
            self._record("ollama-probe-chat-ready", False, "ollama-probe", str(exc))

    def _ollama_raw_detail(self, data: dict[str, Any], content: str) -> dict[str, Any]:
        return {
            "response_chars": len(content),
            "content_preview": content[:240],
            "thinking_chars": len(str(data.get("message", {}).get("thinking", ""))),
            "thinking_preview": str(data.get("message", {}).get("thinking", ""))[:240],
            "done": data.get("done"),
            "done_reason": data.get("done_reason"),
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "prompt_eval_duration": data.get("prompt_eval_duration"),
            "eval_count": data.get("eval_count"),
            "eval_duration": data.get("eval_duration"),
        }

    def _run_ollama_primer(self) -> None:
        try:
            provider = self._ollama_diagnostic_provider(num_predict=32)
            prompts = [
                ("ollama-primer-ready", "Reply with exactly READY.", "ready"),
                ("ollama-primer-token-count", "Reply with exactly COUNT-3.", "count-3"),
                ("ollama-primer-single-label-main-computer", "Reply with exactly YES if this label is visible: main_computer", "yes"),
                ("ollama-primer-single-label-main-computer-test", "Reply with exactly YES if this label is visible: main_computer_test", "yes"),
            ]
            for name, prompt, expected in prompts:
                response = provider.chat([ChatMessage(role="user", content=prompt)])
                lowered = response.content.strip().lower()
                self._record(
                    name,
                    expected in lowered,
                    "ollama-primer",
                    {
                        "expected": expected,
                        "response_chars": len(response.content),
                        "content_preview": response.content[:240],
                        "options": provider.options,
                    },
                )
        except Exception as exc:
            self._record("ollama-primer-error", False, "ollama-primer", str(exc))

    def _run_ollama_visibility(self) -> None:
        try:
            catalog = ProjectCatalog(self.config.workspace)
            provider = self._ollama_diagnostic_provider(num_predict=96)

            primer = provider.chat([ChatMessage(role="user", content="Reply with exactly READY.")])
            primer_ok = "ready" in primer.content.strip().lower()
            self._record(
                "ollama-visibility-primer-responds",
                primer_ok,
                "ollama-visibility",
                {
                    "provider": primer.provider,
                    "model": primer.model,
                    "response_chars": len(primer.content),
                    "content_preview": primer.content[:200],
                    "options": provider.options,
                },
            )
            if not primer_ok:
                return

            project_prompt = (
                "Return only the project labels you can see from this list, comma-separated:\n"
                "main_computer, main_computer_test, main_copmputer_production"
            )
            project_response = provider.chat([ChatMessage(role="user", content=project_prompt)])
            project_content = project_response.content
            project_lowered = project_content.lower()
            expected_projects = ["main_computer", "main_computer_test", "main_copmputer_production"]
            seen_projects = [term for term in expected_projects if term in project_lowered]
            self._record(
                "ollama-visibility-project-labels",
                len(seen_projects) == len(expected_projects),
                "ollama-visibility",
                {
                    "expected_terms": expected_projects,
                    "seen_terms": seen_projects,
                    "missing_terms": [term for term in expected_projects if term not in seen_projects],
                    "content_preview": project_content[:400],
                },
            )

            manifest = catalog.main_computer_manifest(max_files_per_project=40)
            expected_terms = [
                "todo.md",
                "readme.md",
                "viewport.py",
                "diagnostics.py",
            ]
            direct_seen_terms: list[str] = []
            for term in expected_terms:
                matching_lines = [line for line in manifest.splitlines() if term in line.lower()]
                response = provider.chat(
                    [
                        ChatMessage(
                            role="system",
                            content=(
                                "You are testing file visibility only. Answer exactly YES or NO. "
                                "Use only the supplied manifest excerpt."
                            ),
                        ),
                        ChatMessage(
                            role="user",
                            content=(
                                f"Target file label: {term}\n"
                                "Does this excerpt contain that target file label?\n\n"
                                + "\n".join(matching_lines)
                            ),
                        ),
                    ]
                )
                lowered = response.content.strip().lower()
                found = "yes" in lowered or term in lowered
                if found:
                    direct_seen_terms.append(term)
                self._record(
                    f"ollama-visibility-direct-{term.replace('.', '-')}",
                    found,
                    "ollama-visibility",
                    {
                        "target": term,
                        "matching_line_count": len(matching_lines),
                        "matching_lines": matching_lines[:8],
                        "response_chars": len(response.content),
                        "content_preview": response.content[:240],
                    },
                )
            self._record(
                "ollama-visibility-direct-file-rungs",
                len(direct_seen_terms) == len(expected_terms),
                "ollama-visibility",
                {
                    "expected_terms": expected_terms,
                    "seen_terms": direct_seen_terms,
                    "missing_terms": [term for term in expected_terms if term not in direct_seen_terms],
                },
            )

            response = provider.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are testing file visibility only. Use only the supplied manifest. "
                            "Return a terse comma-separated list of visible labels."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            "From this manifest, list only labels that are present: "
                            "TODO.md, README.md, viewport.py, diagnostics.py.\n\n"
                            f"{manifest}"
                        ),
                    ),
                ]
            )
            content = response.content
            lowered = content.lower()
            seen_terms = [term for term in expected_terms if term in lowered]
            self._record(
                "ollama-visibility-provider-responds",
                bool(content.strip()),
                "ollama-visibility",
                {
                    "provider": response.provider,
                    "model": response.model,
                    "response_chars": len(content),
                    "content_preview": content[:1000],
                    "manifest_chars": len(manifest),
                    "options": provider.options,
                },
            )
            self._record(
                "ollama-visibility-main-computer-files",
                len(direct_seen_terms) == len(expected_terms),
                "ollama-visibility",
                {
                    "expected_terms": expected_terms,
                    "direct_seen_terms": direct_seen_terms,
                    "direct_missing_terms": [term for term in expected_terms if term not in direct_seen_terms],
                    "full_manifest_seen_terms": seen_terms,
                    "full_manifest_missing_terms": [term for term in expected_terms if term not in seen_terms],
                },
            )
        except Exception as exc:
            self._record("ollama-visibility-provider-responds", False, "ollama-visibility", str(exc))

    def _get_text(self, url: str) -> str:
        with urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")

    def _get_json(self, url: str) -> dict[str, Any]:
        with urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object from {url}")
        return data

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.config.ollama_timeout_s) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object from {url}")
        return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main-computer diagnostics")
    parser.add_argument("--level", choices=LEVELS, default="widgets", help="Diagnostic depth.")
    parser.add_argument("--workspace", help="Workspace root to scan.")
    parser.add_argument("--provider", choices=["ollama", "openai"], help="Provider for build/live diagnostics.")
    parser.add_argument("--model", help="Provider model name.")
    parser.add_argument("--ollama-base-url", help="Ollama base URL.")
    parser.add_argument("--ollama-timeout-s", type=float, help="Ollama HTTP timeout in seconds.")
    parser.add_argument("--openai-base-url", help="OpenAI-compatible base URL.")
    parser.add_argument("--url", help="Use an already running viewport for server/widget diagnostics.")
    parser.add_argument("--output-dir", default="diagnostics_output", help="Where to write reports and screenshots.")
    parser.add_argument("--headed", action="store_true", help="Show the browser during widget diagnostics.")
    return parser


def config_from_args(args: argparse.Namespace) -> MainComputerConfig:
    base = MainComputerConfig.from_env()
    return MainComputerConfig(
        workspace=Path(args.workspace) if args.workspace else base.workspace,
        provider=args.provider or base.provider,
        model=args.model or base.model,
        patch_level=base.patch_level,
        ollama_base_url=args.ollama_base_url or base.ollama_base_url,
        ollama_timeout_s=args.ollama_timeout_s if args.ollama_timeout_s is not None else base.ollama_timeout_s,
        openai_base_url=args.openai_base_url or base.openai_base_url,
        ollama_debug_passcode=base.ollama_debug_passcode,
        energy_admin_passcode=base.energy_admin_passcode,
    )


def run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    runner = DiagnosticRunner(
        config=config_from_args(args),
        level=args.level,
        output_dir=Path(args.output_dir),
        url=args.url,
        headed=args.headed,
    )
    return runner.run()


def main() -> int:
    report = run_from_args(build_parser().parse_args())
    print(f"Diagnostics passed {len(report['checks'])} checks at level {report['level']}")
    print(f"Report: {Path(report['output_dir']) / 'diagnostics_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
