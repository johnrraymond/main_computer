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


GOOD_OPENING_SHUTTLE_RESPONSE = textwrap.dedent(
    '''
    ```json
    {
      "schema": "game.gameplayPackJsManifest.v1",
      "kind": "gameplay-pack-js",
      "manifestVersion": "gameplay-pack-js.manifest.v1",
      "id": "pack.experimental.large-random-shuttle-boarder",
      "title": "Large Random Shuttle Boarder",
      "version": "0.1.0",
      "entry": "pack.js",
      "targets": {
        "encounter": "opening-shuttle-ambush"
      }
    }
    ```

    ```js
    const RANDOM_LOCATIONS = Object.freeze([
      "shuttle.forward",
      "shuttle.mid",
      "shuttle.aft"
    ]);

    function chooseRandomLocation() {
      return RANDOM_LOCATIONS[Math.floor(Math.random() * RANDOM_LOCATIONS.length)];
    }

    export default defineGameplayPack({
      id: "pack.experimental.large-random-shuttle-boarder",
      title: "Large Random Shuttle Boarder",
      version: "0.1.0",

      setup(pack) {
        pack.encounter("opening-shuttle-ambush", (encounter) => {
          encounter.onStart(() => {
            const location = chooseRandomLocation();
            encounter.showHudMessage("Experimental pack: a large boarder has breached the shuttle.");
            encounter.spawnWave({
              id: "experimental-large-random-shuttle-boarder",
              location,
              actors: [
                {
                  archetype: "shuttle-raider",
                  count: 1,
                  displayName: "Large Shuttle Boarder",
                  healthMultiplier: 2.5,
                  scale: 1.4
                }
              ],
              hudMessage: "A large hostile boarder emerges from a random shuttle location."
            });
          });
        });
      }
    });
    ```
    '''
).strip()


BAD_WRONG_SCENARIO_RESPONSE = textwrap.dedent(
    '''
    ```json
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
    ```

    ```js
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
    ```
    '''
).strip()


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
    """Patch AI: tightened local-AI gameplay-pack generation prompt contract."""

    def run_script(
        self,
        *extra_args: str,
        response_text: str = GOOD_OPENING_SHUTTLE_RESPONSE,
        check: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-"))
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(response_text, encoding="utf-8")
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

    def test_fake_response_generates_experimental_pack_under_output_root(self) -> None:
        result, output_root = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "large-random-shuttle-boarder",
            "--prompt",
            "At the start, spawn one large hostile boarder in a random valid shuttle location.",
            check=True,
        )
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])

        self.assertEqual(pack_dir, output_root / "large-random-shuttle-boarder")
        self.assertTrue((pack_dir / "prompt.txt").exists())
        self.assertTrue((pack_dir / "generation_prompt.md").exists())
        self.assertTrue((pack_dir / "generation.md").exists())
        self.assertTrue((pack_dir / "pack.js").exists())
        self.assertTrue((pack_dir / "manifest.json").exists())
        self.assertTrue((pack_dir / "validation.json").exists())

        manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
        validation = json.loads((pack_dir / "validation.json").read_text(encoding="utf-8"))

        self.assertTrue(payload["ok"])
        self.assertTrue(validation["ok"], validation)
        self.assertEqual(manifest["targets"]["encounter"], "opening-shuttle-ambush")
        self.assertTrue(manifest["experimental"])
        self.assertTrue(manifest["generated"])
        self.assertFalse(manifest["defaultEnabled"])
        self.assertTrue(manifest["hiddenFromLobby"])
        self.assertIn('pack.encounter("opening-shuttle-ambush"', (pack_dir / "pack.js").read_text(encoding="utf-8"))

    def test_generator_prompt_pins_scenario_and_additive_scope(self) -> None:
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
        generation_prompt = Path(payload["files"]["generationPrompt"]).read_text(encoding="utf-8")

        self.assertIn("Existing scenario id: opening-shuttle-ambush", generation_prompt)
        self.assertIn('Required API shape: pack.encounter("opening-shuttle-ambush", ...)', generation_prompt)
        self.assertIn("Do not create a new scenario id.", generation_prompt)
        self.assertIn("Do not modify base scenario files.", generation_prompt)
        self.assertIn("The generated pack must be additive", generation_prompt)
        self.assertIn("BEGIN manifest.json", generation_prompt)
        self.assertIn("END manifest.json", generation_prompt)
        self.assertIn("BEGIN pack.js", generation_prompt)
        self.assertIn("END pack.js", generation_prompt)
        self.assertIn("Do not summarize the examples.", generation_prompt)
        self.assertNotIn("Runtime excerpt for the current pack API", generation_prompt)
        self.assertNotIn("function setObjective", generation_prompt)

    def test_marker_response_generates_experimental_pack(self) -> None:
        result, output_root = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "marker-large-boarder",
            "--prompt",
            "At the start, spawn one large hostile boarder.",
            response_text=GOOD_MARKER_RESPONSE,
            check=True,
        )
        payload = json.loads(result.stdout)
        pack_dir = Path(payload["outputDir"])

        self.assertEqual(pack_dir, output_root / "marker-large-boarder")
        self.assertTrue(payload["ok"], payload)
        self.assertIn(
            'pack.encounter("opening-shuttle-ambush"',
            (pack_dir / "pack.js").read_text(encoding="utf-8"),
        )
        manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["targets"]["encounter"], "opening-shuttle-ambush")
        self.assertFalse(manifest["defaultEnabled"])
        self.assertTrue(manifest["hiddenFromLobby"])

    def test_summary_response_gets_actionable_error(self) -> None:
        result, _ = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "summary-response",
            "--prompt",
            "At the start, spawn one large hostile boarder.",
            response_text=SUMMARY_INSTEAD_OF_PACK_RESPONSE,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(
            "local AI summarized the runtime/examples instead of returning BEGIN pack.js / END pack.js",
            payload["validation"]["errors"][0],
        )

    def test_wrong_scenario_pack_fails_validation(self) -> None:
        result, _ = self.run_script(
            "--scenario",
            "opening-shuttle-ambush",
            "--slug",
            "bad-generated-pack",
            "--prompt",
            "Generate something wrong.",
            response_text=BAD_WRONG_SCENARIO_RESPONSE,
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

    def test_unknown_scenario_is_rejected_before_generation(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gameplay-pack-smoke-unknown-"))
        response_path = temp_dir / "fake-response.md"
        response_path.write_text(GOOD_OPENING_SHUTTLE_RESPONSE, encoding="utf-8")
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
