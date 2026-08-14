from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_generate_gameplay_pack_with_local_ai.py"


GOOD_MARKER_RESPONSE = textwrap.dedent(
    """
    BEGIN manifest.json
    {
      "schema": "game.gameplayPackJsManifest.v1",
      "kind": "gameplay-pack-js",
      "manifestVersion": "gameplay-pack-js.manifest.v1",
      "id": "pack.experimental.marker-large-boarder",
      "title": "Marker Large Boarder",
      "version": "0.1.0",
      "entry": "pack.js",
      "targets": {
        "encounter": "opening-shuttle-ambush"
      }
    }
    END manifest.json

    BEGIN pack.js
    export default defineGameplayPack({
      id: "pack.experimental.marker-large-boarder",
      title: "Marker Large Boarder",
      version: "0.1.0",

      setup(pack) {
        pack.encounter("opening-shuttle-ambush", (encounter) => {
          encounter.onStart(() => {
            encounter.showHudMessage("Experimental marker pack: large shuttle boarder inbound.");
            encounter.spawnWave({
              id: "experimental-marker-large-boarder",
              location: "shuttle.mid",
              actors: [
                {
                  archetype: "shuttle-raider",
                  count: 1,
                  displayName: "Marker Large Boarder",
                  healthMultiplier: 2.5,
                  scale: 1.4
                }
              ],
              hudMessage: "A large boarder appears in the shuttle."
            });
          });
        });
      }
    });
    END pack.js
    """
).strip()


GOOD_PLAN_RESPONSE = textwrap.dedent(
    """
    BEGIN pack_plan.json
    {
      "schema": "game.gameplayPackGenerationPlan.v1",
      "kind": "gameplay-pack-generation-plan",
      "project": "webgl-demo",
      "scenario": {
        "id": "opening-shuttle-ambush",
        "kind": "encounter",
        "apiCall": "pack.encounter(\\"opening-shuttle-ambush\\", ...)"
      },
      "packId": "pack.experimental.large-random-shuttle-boarder",
      "title": "Large Random Shuttle Boarder",
      "summary": "Spawn one large shuttle boarder at encounter start.",
      "steps": [
        {
          "id": "step-1",
          "event": "onStart",
          "summary": "Spawn the large boarder and warn the player.",
          "commands": ["showHudMessage", "spawnWave"]
        }
      ]
    }
    END pack_plan.json
    """
).strip()


BAD_PLAN_RESPONSE = textwrap.dedent(
    """
    BEGIN pack_plan.json
    {
      "schema": "game.gameplayPackGenerationPlan.v1",
      "kind": "gameplay-pack-generation-plan",
      "project": "webgl-demo",
      "scenario": {
        "id": "opening-shuttle-ambush",
        "kind": "encounter"
      },
      "packId": "pack.experimental.bad-plan",
      "title": "Bad Plan",
      "summary": "Use an unsupported event.",
      "steps": [
        {
          "id": "step-1",
          "event": "onMadeUpEvent",
          "summary": "This should fail before pack generation.",
          "commands": ["spawnWave"]
        }
      ]
    }
    END pack_plan.json
    """
).strip()


BAD_WRONG_SCENARIO_RESPONSE = textwrap.dedent(
    """
    BEGIN manifest.json
    {
      "kind": "gameplay-pack-js",
      "id": "pack.experimental.bad",
      "title": "Bad Pack",
      "version": "0.1.0",
      "entry": "pack.js",
      "targets": {
        "section": "made-up-section"
      }
    }
    END manifest.json

    BEGIN pack.js
    export default defineGameplayPack({
      id: "pack.experimental.bad",
      title: "Bad Pack",
      version: "0.1.0",
      setup(pack) {
        pack.section("made-up-section", (section) => {
          section.onCutsceneResolved("made-up-cutscene", () => {
            section.showHudMessage("This should not pass.");
          });
        });
      }
    });
    END pack.js
    """
).strip()



BAD_UNSUPPORTED_ENCOUNTER_API_RESPONSE = textwrap.dedent(
    """
    BEGIN manifest.json
    {
      "kind": "gameplay-pack-js",
      "id": "pack.experimental.unsupported-api",
      "title": "Unsupported API Pack",
      "version": "0.1.0",
      "entry": "pack.js",
      "targets": {
        "encounter": "opening-shuttle-ambush"
      }
    }
    END manifest.json

    BEGIN pack.js
    export default defineGameplayPack({
      id: "pack.experimental.unsupported-api",
      title: "Unsupported API Pack",
      version: "0.1.0",
      setup(pack) {
        pack.encounter("opening-shuttle-ambush", (encounter) => {
          encounter.onStart(() => {
            const locations = encounter.getMetadata("shuttle-spawn-locations");
            encounter.showHudMessage("Unsupported metadata lookup.");
            encounter.spawnWave({
              id: "unsupported-api-wave",
              location: locations[0],
              actors: [{ archetype: "shuttle-raider", count: 1 }]
            });
          });
        });
      }
    });
    END pack.js
    """
).strip()


SUMMARY_INSTEAD_OF_PACK_RESPONSE = textwrap.dedent(
    """
    It looks like you are providing a snippet of a JavaScript utility module used for managing Gameplay Packs.

    The code cuts off mid-function: setObjec. Here is the completed version of that function:

    ```javascript
    setObjective(objective) {
      return gameplayPackEncounterCommand(harness, id, "set-objective", objective);
    }
    ```

    Are you looking to debug a specific part of this logic?
    """
).strip()


class SmokeGenerateGameplayPackWithLocalAiTests(unittest.TestCase):
    """Patch AK: reviewed-plan local-AI gameplay-pack generation workflow."""

    def run_script(
        self,
        *extra_args: str,
        response_text: str = GOOD_MARKER_RESPONSE,
        plan_response_text: str | None = None,
        check: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-"))
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(response_text, encoding="utf-8")
        fake_args = ["--fake-response", str(response_path)]
        if plan_response_text is not None:
            plan_response_path = temp_dir / "fake-plan-response.md"
            plan_response_path.write_text(plan_response_text, encoding="utf-8")
            fake_args.extend(["--fake-plan-response", str(plan_response_path)])
        output_root = temp_dir / "experimental"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(output_root),
                *fake_args,
                *extra_args,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result, output_root

    def write_plan_file(self, temp_dir: Path, *, slug: str = "large-random-shuttle-boarder") -> Path:
        pack_dir = temp_dir / "experimental" / slug
        pack_dir.mkdir(parents=True, exist_ok=True)
        plan_text = GOOD_PLAN_RESPONSE.split("BEGIN pack_plan.json", 1)[1].split("END pack_plan.json", 1)[0].strip()
        plan_path = pack_dir / "plan.json"
        plan_path.write_text(plan_text + "\n", encoding="utf-8")
        (pack_dir / "prompt.txt").write_text(
            "At the start, spawn one large hostile boarder in a random valid shuttle location.\n",
            encoding="utf-8",
        )
        return plan_path

    def test_default_run_generates_reviewable_plan_only(self) -> None:
        result, output_root = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "large-random-shuttle-boarder",
            "--prompt",
            "At the start, spawn one large hostile boarder in a random valid shuttle location.",
            plan_response_text=GOOD_PLAN_RESPONSE,
            check=True,
        )
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])
        validation = json.loads((pack_dir / "validation.json").read_text(encoding="utf-8"))

        self.assertEqual(pack_dir, output_root / "large-random-shuttle-boarder")
        self.assertTrue(payload["ok"], payload)
        self.assertTrue((pack_dir / "prompt.txt").exists())
        self.assertTrue((pack_dir / "generation_plan_prompt.md").exists())
        self.assertTrue((pack_dir / "generation_plan.md").exists())
        self.assertTrue((pack_dir / "plan.json").exists())
        self.assertFalse((pack_dir / "pack.js").exists())
        self.assertFalse((pack_dir / "manifest.json").exists())
        self.assertEqual(validation["details"]["stage"], "plan")
        self.assertTrue(validation["details"]["reviewRequired"])
        self.assertIn("--from-plan", validation["details"]["nextCommand"])

    def test_auto_approve_plan_generates_pack_files_in_one_run_with_fake_responses(self) -> None:
        result, output_root = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "large-random-shuttle-boarder",
            "--prompt",
            "At the start, spawn one large hostile boarder in a random valid shuttle location.",
            "--auto-approve-plan",
            "--overwrite",
            plan_response_text=GOOD_PLAN_RESPONSE,
            check=True,
        )
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])
        validation = json.loads((pack_dir / "validation.json").read_text(encoding="utf-8"))

        self.assertEqual(pack_dir, output_root / "large-random-shuttle-boarder")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["stage"], "pack")
        self.assertTrue(payload["autoApprovedPlan"])
        self.assertEqual(payload["planProvider"], "fake-response")
        self.assertEqual(payload["packProvider"], "fake-response")
        self.assertEqual(payload["aiCallCount"], 0)
        self.assertTrue((pack_dir / "plan.json").exists())
        self.assertTrue((pack_dir / "pack.js").exists())
        self.assertTrue((pack_dir / "manifest.json").exists())
        self.assertTrue(validation["details"]["autoApprovedPlan"])
        self.assertEqual(validation["details"]["sampleEvents"], [{"type": "start"}])
        self.assertEqual(
            validation["details"]["runtime_validation"]["sampleDispatch"]["commandTypes"],
            ["show-hud-message", "spawn-wave"],
        )

    def test_auto_approve_plan_exposes_two_ai_calls_when_local_ai_is_used(self) -> None:
        """--auto-approve-plan intentionally makes one plan call and one pack call."""

        import importlib.util
        import types

        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-auto-approve-"))
        output_root = temp_dir / "experimental"
        module = types.ModuleType("main_computer.local_model_prompt_component_v1")

        def fake_run_local_model_prompt_call(*, prompt_text, output_dir, model=None):
            trace_dir = Path(output_dir)
            trace_dir.mkdir(parents=True, exist_ok=True)
            (trace_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
            response = GOOD_PLAN_RESPONSE if "local_model_plan_call" in str(trace_dir) else GOOD_MARKER_RESPONSE
            return types.SimpleNamespace(
                ok=True,
                details={},
                provided_state={"local_model_response_text": response},
            )

        module.run_local_model_prompt_call = fake_run_local_model_prompt_call
        previous = sys.modules.get("main_computer.local_model_prompt_component_v1")
        sys.modules["main_computer.local_model_prompt_component_v1"] = module
        try:
            spec = importlib.util.spec_from_file_location("smoke_generator_under_test_auto", SCRIPT)
            self.assertIsNotNone(spec)
            smoke = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[spec.name] = smoke
            spec.loader.exec_module(smoke)
            args = smoke.build_parser().parse_args(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output-root",
                    str(output_root),
                    "--scenario",
                    "opening-shuttle-ambush",
                    "--slug",
                    "large-random-shuttle-boarder",
                    "--prompt",
                    "At the start, spawn one large hostile boarder.",
                    "--auto-approve-plan",
                    "--overwrite",
                ]
            )
            payload = smoke.run(args)
        finally:
            if previous is None:
                sys.modules.pop("main_computer.local_model_prompt_component_v1", None)
            else:
                sys.modules["main_computer.local_model_prompt_component_v1"] = previous

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["stage"], "pack")
        self.assertTrue(payload["autoApprovedPlan"])
        self.assertEqual(payload["planProvider"], "local-ai")
        self.assertEqual(payload["packProvider"], "local-ai")
        self.assertEqual(payload["aiCallCount"], 2)
        self.assertEqual([call["stage"] for call in payload["aiCalls"]], ["plan", "pack"])
        self.assertTrue(Path(payload["aiCalls"][0]["traceDir"]).exists())
        self.assertTrue(Path(payload["aiCalls"][1]["traceDir"]).exists())
        validation = json.loads((Path(payload["outputDir"]) / "validation.json").read_text(encoding="utf-8"))
        self.assertTrue(validation["details"]["autoApprovedPlan"])
        self.assertEqual(validation["details"]["aiCallCount"], 2)

    def test_auto_approve_plan_cannot_be_combined_with_from_plan_or_plan_only(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-auto-conflict-"))
        plan_path = self.write_plan_file(temp_dir)

        with_from_plan = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--scenario",
                "opening-shuttle-ambush",
                "--from-plan",
                str(plan_path),
                "--auto-approve-plan",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(with_from_plan.returncode, 0)
        self.assertIn("--from-plan and --auto-approve-plan cannot be combined", with_from_plan.stdout)

        with_plan_only = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--scenario",
                "opening-shuttle-ambush",
                "--prompt",
                "Spawn one large boarder.",
                "--plan-only",
                "--auto-approve-plan",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(with_plan_only.returncode, 0)
        self.assertIn("--plan-only and --auto-approve-plan cannot be combined", with_plan_only.stdout)

    def test_plan_prompt_pins_scenario_and_additive_scope(self) -> None:
        result, _ = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "large-random-shuttle-boarder",
            "--prompt",
            "Spawn one large hostile boarder at the start.",
            check=True,
        )
        payload = json.loads(result.stdout)
        plan_prompt = Path(payload["files"]["generationPlanPrompt"]).read_text(encoding="utf-8")

        self.assertIn("Existing scenario id: opening-shuttle-ambush", plan_prompt)
        self.assertIn('Required API shape: pack.encounter("opening-shuttle-ambush", ...)', plan_prompt)
        self.assertIn("Do not create a new scenario id.", plan_prompt)
        self.assertIn("The generated behavior must be additive", plan_prompt)
        self.assertIn("BEGIN pack_plan.json", plan_prompt)
        self.assertIn("END pack_plan.json", plan_prompt)
        self.assertIn("Do not write pack.js.", plan_prompt)
        self.assertNotIn("Runtime excerpt for the current pack API", plan_prompt)
        self.assertNotIn("function setObjective", plan_prompt)

    def test_additive_only_prompt_rejects_global_hostile_multiplier_plan(self) -> None:
        bad_plan = textwrap.dedent(
            """
            BEGIN pack_plan.json
            {
              "schema": "game.gameplayPackGenerationPlan.v1",
              "kind": "gameplay-pack-generation-plan",
              "project": "webgl-demo",
              "scenario": {
                "id": "opening-shuttle-ambush",
                "kind": "encounter"
              },
              "packId": "pack.experimental.bad-additive-plan",
              "title": "Bad Additive Plan",
              "summary": "Spawn one large boarder.",
              "steps": [
                {
                  "id": "step-1",
                  "event": "onStart",
                  "summary": "Spawn a boarder but also change all hostiles.",
                  "commands": ["setHostileHealthMultiplier", "spawnWave", "showHudMessage"]
                }
              ]
            }
            END pack_plan.json
            """
        ).strip()

        result, _ = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "bad-additive-plan",
            "--prompt",
            "At the start, spawn one large hostile boarder. Additive only.",
            "--auto-approve-plan",
            "--overwrite",
            plan_response_text=bad_plan,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("setHostileHealthMultiplier", "\n".join(payload["validation"]["errors"]))
        self.assertIn("additive only", "\n".join(payload["validation"]["errors"]))
        self.assertFalse((Path(payload["outputDir"]) / "pack.js").exists())

    def test_from_plan_generates_pack_files(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-from-plan-"))
        plan_path = self.write_plan_file(temp_dir)

        response_path = temp_dir / "fake-response.md"
        response_path.write_text(GOOD_MARKER_RESPONSE, encoding="utf-8")
        output_root = temp_dir / "experimental"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(output_root),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--slug",
                "large-random-shuttle-boarder",
                "--from-plan",
                str(plan_path),
                "--overwrite",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])
        validation = json.loads((pack_dir / "validation.json").read_text(encoding="utf-8"))
        manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["planProvider"], "reviewed-plan")
        self.assertEqual(payload["packProvider"], "fake-response")
        self.assertEqual(payload["provider"], "fake-response")
        self.assertEqual(validation["details"]["planProvider"], "reviewed-plan")
        self.assertEqual(validation["details"]["packProvider"], "fake-response")
        self.assertEqual(validation["details"]["aiCallCount"], 0)
        self.assertTrue((pack_dir / "pack_generation_prompt.md").exists())
        self.assertTrue((pack_dir / "generation.md").exists())
        self.assertTrue((pack_dir / "pack.js").exists())
        self.assertEqual(validation["details"]["stage"], "pack")
        self.assertIn(str(plan_path), validation["details"]["fromPlan"])
        self.assertEqual(validation["details"]["sampleEvents"], [{"type": "start"}])
        sample_dispatch = validation["details"]["runtime_validation"]["sampleDispatch"]
        self.assertEqual(sample_dispatch["scenarioKind"], "encounter")
        self.assertEqual(sample_dispatch["scenarioId"], "opening-shuttle-ambush")
        self.assertEqual(sample_dispatch["commandCount"], 2)
        self.assertEqual(sample_dispatch["commandTypes"], ["show-hud-message", "spawn-wave"])
        self.assertEqual(sample_dispatch["harnessSummary"]["commandCount"], 2)
        self.assertEqual(manifest["targets"]["encounter"], "opening-shuttle-ambush")
        self.assertFalse(manifest["defaultEnabled"])
        self.assertTrue(manifest["hiddenFromLobby"])
        self.assertIn(
            'pack.encounter("opening-shuttle-ambush"',
            (pack_dir / "pack.js").read_text(encoding="utf-8"),
        )

    def test_from_plan_reads_sibling_prompt_and_overwrite_does_not_delete_plan_before_reading(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-from-plan-overwrite-"))
        plan_path = self.write_plan_file(temp_dir, slug="same-folder-plan")

        response_path = temp_dir / "fake-response.md"
        response_path.write_text(GOOD_MARKER_RESPONSE, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--from-plan",
                str(plan_path),
                "--overwrite",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])

        self.assertEqual(pack_dir.name, "same-folder-plan")
        self.assertTrue((pack_dir / "pack.js").exists())
        self.assertIn(
            "At the start, spawn one large hostile boarder",
            (pack_dir / "prompt.txt").read_text(encoding="utf-8"),
        )

    def test_bad_plan_fails_before_pack_generation(self) -> None:
        result, _ = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "bad-plan",
            "--prompt",
            "Use an unsupported event.",
            plan_response_text=BAD_PLAN_RESPONSE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])
        self.assertFalse(payload["ok"])
        self.assertFalse((pack_dir / "pack.js").exists())
        self.assertIn("onMadeUpEvent", payload["validation"]["errors"][0])
        self.assertEqual(payload["validation"]["details"]["stage"], "plan")

    def test_summary_response_gets_actionable_error_when_generating_from_plan(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-summary-"))
        plan_path = self.write_plan_file(temp_dir)
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(SUMMARY_INSTEAD_OF_PACK_RESPONSE, encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--slug",
                "summary-response",
                "--from-plan",
                str(plan_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(
            "local AI summarized the runtime/examples instead of returning BEGIN pack.js / END pack.js",
            payload["validation"]["errors"][0],
        )

    def test_wrong_scenario_pack_fails_validation_when_generating_from_plan(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-wrong-scenario-"))
        plan_path = self.write_plan_file(temp_dir)
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(BAD_WRONG_SCENARIO_RESPONSE, encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--slug",
                "bad-generated-pack",
                "--from-plan",
                str(plan_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        validation = payload["validation"]
        self.assertFalse(payload["ok"])
        self.assertFalse(validation["ok"])
        self.assertIn(
            'pack.js must register the requested scenario with pack.encounter("opening-shuttle-ambush", ...)',
            validation["errors"],
        )
        self.assertIn("encounter smoke target must not use pack.section(...)", validation["errors"])


    def test_pack_generation_prompt_lists_only_supported_context_methods(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-pack-prompt-"))
        plan_path = self.write_plan_file(temp_dir)

        response_path = temp_dir / "fake-response.md"
        response_path.write_text(GOOD_MARKER_RESPONSE, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--slug",
                "prompt-api-rules",
                "--from-plan",
                str(plan_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        pack_prompt = Path(payload["files"]["packGenerationPrompt"]).read_text(encoding="utf-8")

        self.assertIn("ONLY AVAILABLE ENCOUNTER METHODS", pack_prompt)
        self.assertIn('"onStart"', pack_prompt)
        self.assertIn('"spawnWave"', pack_prompt)
        self.assertIn("Do not call encounter.getMetadata(...)", pack_prompt)
        self.assertIn("define a local constant array in pack.js", pack_prompt)

    def test_unsupported_encounter_context_api_fails_before_runtime_guesswork(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-unsupported-api-"))
        plan_path = self.write_plan_file(temp_dir)
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(BAD_UNSUPPORTED_ENCOUNTER_API_RESPONSE, encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "opening-shuttle-ambush",
                "--slug",
                "unsupported-api",
                "--from-plan",
                str(plan_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        validation = payload["validation"]
        self.assertFalse(validation["ok"])
        self.assertIn(
            "unsupported encounter API call encounter.getMetadata(...)",
            "\n".join(validation["errors"]),
        )
        unsupported = validation["details"]["unsupportedContextApiCalls"]
        self.assertEqual(unsupported[0]["context"], "encounter")
        self.assertEqual(unsupported[0]["method"], "getMetadata")



    def test_plan_generation_result_exposes_ai_call_audit_when_local_ai_is_used(self) -> None:
        """Output should not hide local model use behind generic provider fields."""

        import types

        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-audit-plan-"))
        output_root = temp_dir / "experimental"
        module = types.ModuleType("main_computer.local_model_prompt_component_v1")

        def fake_run_local_model_prompt_call(*, prompt_text, output_dir, model=None):
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            (Path(output_dir) / "prompt.txt").write_text(prompt_text, encoding="utf-8")
            return types.SimpleNamespace(
                ok=True,
                details={},
                provided_state={"local_model_response_text": GOOD_PLAN_RESPONSE},
            )

        module.run_local_model_prompt_call = fake_run_local_model_prompt_call
        previous = sys.modules.get("main_computer.local_model_prompt_component_v1")
        sys.modules["main_computer.local_model_prompt_component_v1"] = module
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("smoke_generator_under_test", SCRIPT)
            self.assertIsNotNone(spec)
            smoke = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[spec.name] = smoke
            spec.loader.exec_module(smoke)
            args = smoke.build_parser().parse_args(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output-root",
                    str(output_root),
                    "--scenario",
                    "opening-shuttle-ambush",
                    "--slug",
                    "large-random-shuttle-boarder",
                    "--prompt",
                    "At the start, spawn one large hostile boarder.",
                ]
            )
            payload = smoke.run(args)
        finally:
            if previous is None:
                sys.modules.pop("main_computer.local_model_prompt_component_v1", None)
            else:
                sys.modules["main_computer.local_model_prompt_component_v1"] = previous

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["stage"], "plan")
        self.assertEqual(payload["planProvider"], "local-ai")
        self.assertIsNone(payload["packProvider"])
        self.assertEqual(payload["aiCallCount"], 1)
        self.assertEqual(payload["aiCalls"][0]["stage"], "plan")
        self.assertEqual(payload["aiCalls"][0]["provider"], "local-ai")
        self.assertTrue(Path(payload["aiCalls"][0]["traceDir"]).exists())
        validation = json.loads((Path(payload["outputDir"]) / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(validation["details"]["aiCallCount"], 1)
        self.assertEqual(validation["details"]["aiCalls"][0]["stage"], "plan")

    def test_from_plan_result_exposes_pack_ai_call_audit_when_local_ai_is_used(self) -> None:
        """--from-plan uses reviewed input for the plan and one local-AI call for pack files."""

        import types

        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-audit-pack-"))
        plan_path = self.write_plan_file(temp_dir)
        output_root = temp_dir / "experimental"
        module = types.ModuleType("main_computer.local_model_prompt_component_v1")

        def fake_run_local_model_prompt_call(*, prompt_text, output_dir, model=None):
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            (Path(output_dir) / "prompt.txt").write_text(prompt_text, encoding="utf-8")
            return types.SimpleNamespace(
                ok=True,
                details={},
                provided_state={"local_model_response_text": GOOD_MARKER_RESPONSE},
            )

        module.run_local_model_prompt_call = fake_run_local_model_prompt_call
        previous = sys.modules.get("main_computer.local_model_prompt_component_v1")
        sys.modules["main_computer.local_model_prompt_component_v1"] = module
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("smoke_generator_under_test_pack", SCRIPT)
            self.assertIsNotNone(spec)
            smoke = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            sys.modules[spec.name] = smoke
            spec.loader.exec_module(smoke)
            args = smoke.build_parser().parse_args(
                [
                    "--repo-root",
                    str(ROOT),
                    "--output-root",
                    str(output_root),
                    "--scenario",
                    "opening-shuttle-ambush",
                    "--slug",
                    "large-random-shuttle-boarder",
                    "--from-plan",
                    str(plan_path),
                    "--overwrite",
                ]
            )
            payload = smoke.run(args)
        finally:
            if previous is None:
                sys.modules.pop("main_computer.local_model_prompt_component_v1", None)
            else:
                sys.modules["main_computer.local_model_prompt_component_v1"] = previous

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["stage"], "pack")
        self.assertEqual(payload["planProvider"], "reviewed-plan")
        self.assertEqual(payload["packProvider"], "local-ai")
        self.assertEqual(payload["provider"], "local-ai")
        self.assertEqual(payload["aiCallCount"], 1)
        self.assertEqual(payload["aiCalls"][0]["stage"], "pack")
        self.assertEqual(payload["aiCalls"][0]["provider"], "local-ai")
        self.assertTrue(Path(payload["aiCalls"][0]["traceDir"]).exists())
        validation = json.loads((Path(payload["outputDir"]) / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(validation["details"]["planProvider"], "reviewed-plan")
        self.assertEqual(validation["details"]["packProvider"], "local-ai")
        self.assertEqual(validation["details"]["aiCallCount"], 1)
        self.assertEqual(validation["details"]["aiCalls"][0]["stage"], "pack")


    def test_unknown_scenario_is_rejected_before_generation(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-unknown-"))
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(GOOD_MARKER_RESPONSE, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(ROOT),
                "--output-root",
                str(temp_dir / "experimental"),
                "--fake-response",
                str(response_path),
                "--scenario",
                "not-a-current-scenario",
                "--prompt",
                "Try a wrong scenario.",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("opening-shuttle-ambush", payload["knownScenarios"])
        self.assertIn("main-ship-bay", payload["knownScenarios"])


if __name__ == "__main__":
    unittest.main()
