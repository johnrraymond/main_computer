from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "main_computer" / "web" / "applications" / "scripts"
SCENARIO_RUNTIME = SCRIPT_ROOT / "system-scenario-runtime.js"
WEBGL_DESKTOP = SCRIPT_ROOT / "webgl-desktop.js"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
GAME_EDITOR_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "game-editor.css"
PROJECT_PATH = ROOT / "game_projects" / "webgl-demo" / "project.json"


class SystemScenarioGeneratedCatalogTests(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for system scenario runtime tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(SCENARIO_RUNTIME), str(PROJECT_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def run_webgl_desktop_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for webgl desktop pack selector tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(WEBGL_DESKTOP)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_runtime_reads_generated_catalog_without_merging_into_base_scenarios(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const generatedCatalog = {
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              exported: true,
              runtimeLoaded: false,
              projectJsonModified: false,
              activatedInRuntime: false,
              enabledPluginIds: ["plugin.hand-authored.vela-cave-extension.001"],
              entryPoints: ["scenario.plugin.vela-cave-extension.followup"],
              scenarioIds: ["scenario.plugin.vela-cave-extension.followup"],
              encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
              receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
              consequenceIds: ["consequence.plugin.vela-cave-extension.route-charted"],
              documentPaths: [
                "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
                "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json"
              ],
              documents: [
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "scenario",
                  id: "scenario.plugin.vela-cave-extension.followup",
                  title: "Vela Cave Extension Followup",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
                  stageIds: ["route-opens", "escape-route", "extracted"],
                  encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"]
                },
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "encounter",
                  id: "encounter.plugin.vela-cave-extension.escape-route",
                  title: "Vela Cave Extension Escape Route",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json",
                  template: "encounter-template.cave-combat-run",
                  objectiveTypes: [
                    "objective-type.recover-item",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-extraction"
                  ],
                  actorArchetypes: ["actor-archetype.vela-cave-guard"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
                  consequenceTypes: ["consequence-type.record-receipt"]
                }
              ],
              scenarios: [],
              encounters: [],
              plugins: [],
              problems: []
            };
            const runtime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog
            });
            let startGeneratedError = "";
            try {
              runtime.startScenario("scenario.plugin.vela-cave-extension.followup");
            } catch (error) {
              startGeneratedError = String(error.message || error);
            }
            const summary = runtime.summary();
            console.log(JSON.stringify({
              baseScenarioCount: summary.scenarios.length,
              baseScenarioLookup: !!runtime.scenarioDefinition("scenario.pax.neutrality-under-fire"),
              generatedBaseLookup: runtime.scenarioDefinition("scenario.plugin.vela-cave-extension.followup"),
              generatedView: runtime.view("scenario.plugin.vela-cave-extension.followup"),
              generatedScenario: runtime.generatedScenarioDefinition("scenario.plugin.vela-cave-extension.followup"),
              generatedEncounter: runtime.generatedEncounterDefinition("encounter.plugin.vela-cave-extension.escape-route"),
              generatedScenarioCount: runtime.generatedScenarioDefinitions().length,
              generatedEncounterCount: runtime.generatedEncounterDefinitions().length,
              summaryGenerated: summary.generatedGameplayCatalog,
              availability: runtime.generatedScenarioAvailability(),
              availabilityCards: runtime.generatedScenarioAvailabilityCards(),
              availabilityCard: runtime.generatedScenarioAvailabilityCard("scenario.plugin.vela-cave-extension.followup"),
              startPreview: runtime.generatedScenarioStartPreview("scenario.plugin.vela-cave-extension.followup"),
              dryRunStartPreview: runtime.dryRunStartGeneratedScenario("scenario.plugin.vela-cave-extension.followup"),
              startGeneratedCommand: runtime.startGeneratedScenario("scenario.plugin.vela-cave-extension.followup"),
              generatedStartCommands: runtime.generatedScenarioStartCommands(),
              pureStartGeneratedCommand: scenarioApi.generatedScenarioStartCommandGate(
                generatedCatalog,
                "scenario.plugin.vela-cave-extension.followup"
              ),
              startPreviews: runtime.generatedScenarioStartPreviews(),
              pureStartPreview: scenarioApi.generatedScenarioStartPreview(
                generatedCatalog,
                "scenario.plugin.vela-cave-extension.followup"
              ),
              summaryStartPreviews: summary.generatedScenarioStartPreviews,
              summaryStartCommands: summary.generatedScenarioStartCommands,
              unknownStartPreview: runtime.generatedScenarioStartPreview("scenario.plugin.vela-cave-extension.missing"),
              unknownStartGeneratedCommand: runtime.startGeneratedScenario("scenario.plugin.vela-cave-extension.missing"),
              summaryAvailability: summary.generatedScenarioAvailability,
              pureAvailability: scenarioApi.generatedScenarioAvailability(generatedCatalog),
              catalog: runtime.generatedGameplayCatalog(),
              startGeneratedError
            }));
            '''
        )

        self.assertEqual(result["baseScenarioCount"], 1)
        self.assertTrue(result["baseScenarioLookup"])
        self.assertIsNone(result["generatedBaseLookup"])
        self.assertIsNone(result["generatedView"])
        self.assertIn("Unknown scenario", result["startGeneratedError"])
        self.assertEqual(result["generatedScenarioCount"], 1)
        self.assertEqual(result["generatedEncounterCount"], 1)
        self.assertEqual(
            result["generatedScenario"]["id"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertTrue(result["generatedScenario"]["readOnly"])
        self.assertTrue(result["generatedScenario"]["generated"])
        self.assertEqual(
            result["generatedEncounter"]["template"],
            "encounter-template.cave-combat-run",
        )
        self.assertEqual(result["summaryGenerated"]["status"], "ready")
        self.assertTrue(result["summaryGenerated"]["ready"])
        self.assertTrue(result["summaryGenerated"]["readOnly"])
        self.assertFalse(result["summaryGenerated"]["runtimeLoaded"])
        self.assertFalse(result["summaryGenerated"]["projectJsonModified"])
        self.assertFalse(result["summaryGenerated"]["activatedInRuntime"])
        self.assertEqual(
            result["summaryGenerated"]["entryPoints"],
            ["scenario.plugin.vela-cave-extension.followup"],
        )
        self.assertEqual(result["availability"]["status"], "ready")
        self.assertTrue(result["availability"]["ready"])
        self.assertTrue(result["availability"]["readOnly"])
        self.assertFalse(result["availability"]["runtimeLoaded"])
        self.assertFalse(result["availability"]["projectJsonModified"])
        self.assertFalse(result["availability"]["activatedInRuntime"])
        self.assertEqual(result["availability"]["count"], 1)
        self.assertEqual(
            result["availability"]["scenarioIds"],
            ["scenario.plugin.vela-cave-extension.followup"],
        )
        self.assertEqual(
            result["availability"]["entryPointScenarioIds"],
            ["scenario.plugin.vela-cave-extension.followup"],
        )
        self.assertEqual(len(result["availabilityCards"]), 1)
        self.assertEqual(
            result["availabilityCard"]["schema"],
            "game.generatedScenarioAvailability.v1",
        )
        self.assertEqual(
            result["availabilityCard"]["kind"],
            "generated-scenario-card",
        )
        self.assertEqual(
            result["availabilityCard"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["availabilityCard"]["pluginId"],
            "plugin.hand-authored.vela-cave-extension.001",
        )
        self.assertTrue(result["availabilityCard"]["entryPoint"])
        self.assertTrue(result["availabilityCard"]["available"])
        self.assertFalse(result["availabilityCard"]["startable"])
        self.assertEqual(result["availabilityCard"]["activationStatus"], "read-only")
        self.assertEqual(
            result["availabilityCard"]["encounterTemplates"],
            ["encounter-template.cave-combat-run"],
        )
        self.assertIn(
            "objective-type.clear-hostiles",
            result["availabilityCard"]["objectiveTypes"],
        )
        self.assertIn(
            "actor-archetype.vela-cave-guard",
            result["availabilityCard"]["actorArchetypes"],
        )
        self.assertEqual(
            result["startPreview"]["schema"],
            "game.generatedScenarioStartPreview.v1",
        )
        self.assertEqual(
            result["startPreview"]["kind"],
            "generated-scenario-start-preview",
        )
        self.assertTrue(result["startPreview"]["dryRun"])
        self.assertTrue(result["startPreview"]["readOnly"])
        self.assertFalse(result["startPreview"]["startable"])
        self.assertFalse(result["startPreview"]["startableInRuntime"])
        self.assertFalse(result["startPreview"]["runtimeLoaded"])
        self.assertFalse(result["startPreview"]["projectJsonModified"])
        self.assertFalse(result["startPreview"]["activatedInRuntime"])
        self.assertTrue(result["startPreview"]["canPreviewStart"])
        self.assertTrue(result["startPreview"]["couldStartAfterRuntimeActivation"])
        self.assertEqual(result["startPreview"]["activationStatus"], "preview-only")
        self.assertEqual(
            result["startPreview"]["reason"],
            "generated-runtime-start-not-implemented",
        )
        self.assertEqual(
            result["startPreview"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["startPreview"]["plan"]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertEqual(
            result["startPreview"]["plan"]["primaryEncounterId"],
            "encounter.plugin.vela-cave-extension.escape-route",
        )
        self.assertEqual(
            result["startPreview"]["plan"]["scenario"]["stageIds"],
            ["route-opens", "escape-route", "extracted"],
        )
        self.assertIn(
            "objective-type.reach-extraction",
            result["startPreview"]["plan"]["objectiveTypes"],
        )
        self.assertEqual(
            result["startPreview"]["plan"]["encounters"][0]["template"],
            "encounter-template.cave-combat-run",
        )
        self.assertEqual(
            result["startPreview"]["plan"]["operations"][0]["kind"],
            "read-generated-scenario",
        )
        self.assertEqual(
            result["dryRunStartPreview"]["options"]["source"],
            "dry-run-start-generated-scenario",
        )
        self.assertEqual(result["dryRunStartPreview"]["plan"], result["startPreview"]["plan"])
        self.assertEqual(
            result["startGeneratedCommand"]["schema"],
            "game.generatedScenarioStartCommand.v1",
        )
        self.assertEqual(
            result["startGeneratedCommand"]["kind"],
            "generated-scenario-start-command",
        )
        self.assertTrue(result["startGeneratedCommand"]["knownGeneratedScenario"])
        self.assertFalse(result["startGeneratedCommand"]["accepted"])
        self.assertTrue(result["startGeneratedCommand"]["blocked"])
        self.assertFalse(result["startGeneratedCommand"]["startable"])
        self.assertFalse(result["startGeneratedCommand"]["startableInRuntime"])
        self.assertFalse(result["startGeneratedCommand"]["runtimeLoaded"])
        self.assertFalse(result["startGeneratedCommand"]["activatedInRuntime"])
        self.assertEqual(result["startGeneratedCommand"]["commandStatus"], "blocked")
        self.assertEqual(
            result["startGeneratedCommand"]["activationStatus"],
            "runtime-activation-disabled",
        )
        self.assertEqual(
            result["startGeneratedCommand"]["reason"],
            "generated-runtime-activation-disabled",
        )
        self.assertEqual(
            result["startGeneratedCommand"]["previewReason"],
            "generated-runtime-start-not-implemented",
        )
        self.assertEqual(result["startGeneratedCommand"]["plan"], result["startPreview"]["plan"])
        self.assertEqual(result["startGeneratedCommand"]["preview"]["plan"], result["startPreview"]["plan"])
        self.assertEqual(
            result["startGeneratedCommand"]["preview"]["options"]["source"],
            "generated-scenario-start-command",
        )
        self.assertEqual(result["pureStartGeneratedCommand"], result["startGeneratedCommand"])
        self.assertEqual(result["summaryStartCommands"], result["generatedStartCommands"])
        self.assertEqual(len(result["generatedStartCommands"]), 1)
        self.assertEqual(result["generatedStartCommands"][0], result["startGeneratedCommand"])
        self.assertEqual(result["pureStartPreview"], result["startPreview"])
        self.assertEqual(result["summaryStartPreviews"], result["startPreviews"])
        self.assertEqual(len(result["startPreviews"]), 1)
        self.assertEqual(result["startPreviews"][0], result["startPreview"])
        self.assertFalse(result["unknownStartPreview"]["canPreviewStart"])
        self.assertIsNone(result["unknownStartPreview"]["plan"])
        self.assertEqual(
            result["unknownStartPreview"]["reason"],
            "unknown-generated-scenario",
        )
        self.assertFalse(result["unknownStartGeneratedCommand"]["knownGeneratedScenario"])
        self.assertTrue(result["unknownStartGeneratedCommand"]["blocked"])
        self.assertFalse(result["unknownStartGeneratedCommand"]["accepted"])
        self.assertEqual(result["unknownStartGeneratedCommand"]["commandStatus"], "blocked")
        self.assertEqual(result["unknownStartGeneratedCommand"]["activationStatus"], "unavailable")
        self.assertEqual(
            result["unknownStartGeneratedCommand"]["reason"],
            "unknown-generated-scenario",
        )
        self.assertIsNone(result["unknownStartGeneratedCommand"]["plan"])
        self.assertEqual(result["summaryAvailability"], result["availability"])
        self.assertEqual(result["pureAvailability"], result["availability"])
        self.assertEqual(result["catalog"]["status"], "ready")
        self.assertEqual(result["catalog"]["problems"], [])

    def test_missing_or_rejected_catalog_is_safe_and_read_only(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const absent = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-absent",
              storage: null,
              restore: false
            });
            const rejectedCatalog = scenarioApi.normalizeGeneratedGameplayCatalog({
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              runtimeLoaded: true,
              projectJsonModified: false,
              activatedInRuntime: false,
              entryPoints: ["scenario.generated.missing"],
              scenarioIds: [],
              documents: [],
              problems: []
            });
            console.log(JSON.stringify({
              absentSummary: absent.summary().generatedGameplayCatalog,
              absentAvailability: absent.generatedScenarioAvailability(),
              rejectedAvailability: scenarioApi.generatedScenarioAvailability(rejectedCatalog),
              absentStartPreviews: absent.generatedScenarioStartPreviews(),
              absentStartCommands: absent.generatedScenarioStartCommands(),
              absentStartPreview: absent.generatedScenarioStartPreview("scenario.generated.missing"),
              absentStartCommand: absent.startGeneratedScenario("scenario.generated.missing"),
              rejectedStartPreview: scenarioApi.generatedScenarioStartPreview(
                rejectedCatalog,
                "scenario.generated.missing"
              ),
              rejectedCatalog
            }));
            '''
        )
        self.assertEqual(result["absentSummary"]["status"], "absent")
        self.assertFalse(result["absentSummary"]["ready"])
        self.assertEqual(result["absentSummary"]["scenarioCount"], 0)
        self.assertEqual(result["absentAvailability"]["status"], "absent")
        self.assertFalse(result["absentAvailability"]["ready"])
        self.assertEqual(result["absentAvailability"]["cards"], [])
        self.assertEqual(result["absentStartPreviews"], [])
        self.assertEqual(result["absentStartCommands"], [])
        self.assertTrue(result["absentStartCommand"]["blocked"])
        self.assertFalse(result["absentStartCommand"]["knownGeneratedScenario"])
        self.assertEqual(result["absentStartCommand"]["reason"], "unknown-generated-scenario")
        self.assertFalse(result["absentStartPreview"]["canPreviewStart"])
        self.assertIsNone(result["absentStartPreview"]["plan"])
        self.assertEqual(
            result["absentStartPreview"]["reason"],
            "unknown-generated-scenario",
        )
        self.assertFalse(result["rejectedAvailability"]["ready"])
        self.assertEqual(result["rejectedAvailability"]["cards"], [])
        self.assertFalse(result["rejectedStartPreview"]["canPreviewStart"])
        self.assertIsNone(result["rejectedStartPreview"]["plan"])
        self.assertEqual(
            result["rejectedStartPreview"]["reason"],
            "unknown-generated-scenario",
        )
        self.assertFalse(result["rejectedCatalog"]["ready"])
        self.assertFalse(result["rejectedCatalog"]["runtimeLoaded"])
        self.assertFalse(result["rejectedCatalog"]["projectJsonModified"])
        self.assertFalse(result["rejectedCatalog"]["activatedInRuntime"])
        self.assertIn(
            "generated gameplay catalog claims runtime loading",
            result["rejectedCatalog"]["problems"],
        )
        self.assertIn(
            "generated gameplay catalog entry point is not a generated scenario: scenario.generated.missing",
            result["rejectedCatalog"]["problems"],
        )


    def test_generated_scenario_activation_flag_accepts_preview_shell_without_mutating_base_state(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const generatedCatalog = {
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              exported: true,
              runtimeLoaded: false,
              projectJsonModified: false,
              activatedInRuntime: false,
              enabledPluginIds: ["plugin.hand-authored.vela-cave-extension.001"],
              entryPoints: ["scenario.plugin.vela-cave-extension.followup"],
              scenarioIds: ["scenario.plugin.vela-cave-extension.followup"],
              encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
              receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
              consequenceIds: ["consequence.plugin.vela-cave-extension.route-charted"],
              documents: [
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "scenario",
                  id: "scenario.plugin.vela-cave-extension.followup",
                  title: "Vela Cave Extension Followup",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
                  stageIds: ["route-opens", "escape-route", "extracted"],
                  encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"]
                },
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "encounter",
                  id: "encounter.plugin.vela-cave-extension.escape-route",
                  title: "Vela Cave Extension Escape Route",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json",
                  template: "encounter-template.cave-combat-run",
                  objectiveTypes: [
                    "objective-type.recover-item",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-extraction"
                  ],
                  actorArchetypes: ["actor-archetype.vela-cave-guard"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
                  consequenceTypes: ["consequence-type.record-receipt"]
                }
              ],
              scenarios: [],
              encounters: [],
              plugins: [],
              problems: []
            };
            const disabledRuntime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-disabled-generated",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog
            });
            const enabledRuntime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-enabled-generated",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog,
              generatedScenarioActivationEnabled: true
            });
            let baseStartError = "";
            try {
              enabledRuntime.startScenario("scenario.plugin.vela-cave-extension.followup");
            } catch (error) {
              baseStartError = String(error.message || error);
            }
            const accepted = enabledRuntime.startGeneratedScenario(
              "scenario.plugin.vela-cave-extension.followup"
            );
            const currentPreviewAfterStart = enabledRuntime.currentGeneratedScenarioPreview();
            const eventsAfterStart = enabledRuntime.generatedScenarioPreviewEvents();
            const summaryAfterStart = enabledRuntime.summary();
            const summaryCurrentPreviewAfterStart = summaryAfterStart.currentGeneratedScenarioPreview;
            const summaryEventsAfterStart = summaryAfterStart.generatedScenarioPreviewEvents;
            const activationStatusAfterStart = enabledRuntime.generatedScenarioActivationStatus();
            const pureAccepted = scenarioApi.generatedScenarioStartCommandGate(
              generatedCatalog,
              "scenario.plugin.vela-cave-extension.followup",
              {runtimeActivationEnabled: true}
            );
            const purePreviewState = scenarioApi.generatedScenarioPreviewShellState(pureAccepted, {
              sequence: 0,
              activeSystemId: ""
            });
            const purePreviewEvent = scenarioApi.generatedScenarioPreviewEventRecord("preview-created", {
              reason: pureAccepted.reason,
              active: true,
              current: purePreviewState
            }, {
              eventSequence: 1,
              runtimeSequence: 0,
              activeSystemId: ""
            });
            const blocked = disabledRuntime.startGeneratedScenario(
              "scenario.plugin.vela-cave-extension.followup"
            );
            const disabledEventsAfterBlockedStart = disabledRuntime.generatedScenarioPreviewEvents();
            const enabledStatusRuntime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-enabled-generated-status",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog,
              generatedScenarioActivationEnabled: true
            });
            const activationStatusBefore = enabledStatusRuntime.generatedScenarioActivationStatus();
            const summaryCommands = enabledStatusRuntime.summary().generatedScenarioStartCommands;
            const abortResult = enabledRuntime.abortGeneratedScenarioPreview("test-abort-generated-preview");
            const afterAbortPreview = enabledRuntime.currentGeneratedScenarioPreview();
            const eventsAfterAbort = enabledRuntime.generatedScenarioPreviewEvents();
            const acceptedAgain = enabledRuntime.startGeneratedScenario(
              "scenario.plugin.vela-cave-extension.followup"
            );
            const currentPreviewBeforeToggleOff = enabledRuntime.currentGeneratedScenarioPreview();
            const eventsAfterAcceptedAgain = enabledRuntime.generatedScenarioPreviewEvents();
            const toggledOff = enabledRuntime.setGeneratedScenarioActivationEnabled(false);
            const previewAfterToggleOff = enabledRuntime.currentGeneratedScenarioPreview();
            const eventsAfterToggleOff = enabledRuntime.generatedScenarioPreviewEvents();
            const blockedAfterToggle = enabledRuntime.startGeneratedScenario(
              "scenario.plugin.vela-cave-extension.followup"
            );
            console.log(JSON.stringify({
              accepted,
              pureAccepted,
              purePreviewState,
              purePreviewEvent,
              currentPreviewAfterStart,
              eventsAfterStart,
              summaryCurrentPreviewAfterStart,
              summaryEventsAfterStart,
              activationStatusAfterStart,
              blocked,
              disabledEventsAfterBlockedStart,
              abortResult,
              afterAbortPreview,
              eventsAfterAbort,
              acceptedAgain,
              currentPreviewBeforeToggleOff,
              eventsAfterAcceptedAgain,
              toggledOff,
              previewAfterToggleOff,
              eventsAfterToggleOff,
              blockedAfterToggle,
              activationStatusBefore,
              summaryActivation: enabledRuntime.summary().generatedScenarioActivation,
              summaryCommands,
              generatedView: enabledRuntime.view("scenario.plugin.vela-cave-extension.followup"),
              baseScenarioLookup: enabledRuntime.scenarioDefinition("scenario.plugin.vela-cave-extension.followup"),
              baseStartError,
              scenarioCount: enabledRuntime.summary().scenarios.length
            }));
            """
        )

        self.assertFalse(result["blocked"]["accepted"])
        self.assertTrue(result["blocked"]["blocked"])
        self.assertEqual(result["blocked"]["commandStatus"], "blocked")
        self.assertEqual(result["blocked"]["activationStatus"], "runtime-activation-disabled")

        self.assertTrue(result["accepted"]["accepted"])
        self.assertFalse(result["accepted"]["blocked"])
        self.assertEqual(result["accepted"]["commandStatus"], "accepted-preview-shell")
        self.assertEqual(result["accepted"]["activationStatus"], "preview-shell")
        self.assertEqual(
            result["accepted"]["reason"],
            "generated-runtime-preview-shell-created",
        )
        self.assertFalse(result["accepted"]["startableInRuntime"])
        self.assertFalse(result["accepted"]["runtimeLoaded"])
        self.assertFalse(result["accepted"]["activatedInRuntime"])
        self.assertIsNotNone(result["accepted"]["previewShell"])
        self.assertEqual(
            result["accepted"]["previewShell"]["kind"],
            "generated-scenario-preview-shell",
        )
        self.assertEqual(
            result["accepted"]["previewShell"]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertIn(
            "objective-type.clear-hostiles",
            result["accepted"]["previewShell"]["objectiveTypes"],
        )
        self.assertFalse(result["accepted"]["previewShell"]["activation"]["rendererHandoff"])
        self.assertFalse(result["accepted"]["previewShell"]["activation"]["gameplayTemplateExecution"])
        self.assertFalse(result["accepted"]["previewShell"]["activation"]["projectJsonModified"])
        self.assertFalse(result["accepted"]["previewShell"]["activation"]["saveStateMutated"])
        self.assertEqual(result["pureAccepted"], result["accepted"])
        self.assertEqual(result["summaryCommands"][0], result["accepted"])

        self.assertIsNotNone(result["currentPreviewAfterStart"])
        self.assertEqual(
            result["currentPreviewAfterStart"]["schema"],
            "game.generatedScenarioPreviewShellState.v1",
        )
        self.assertEqual(
            result["currentPreviewAfterStart"]["kind"],
            "generated-scenario-preview-shell-state",
        )
        self.assertEqual(result["currentPreviewAfterStart"]["status"], "active-preview-shell")
        self.assertTrue(result["currentPreviewAfterStart"]["active"])
        self.assertEqual(
            result["currentPreviewAfterStart"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["currentPreviewAfterStart"]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertFalse(result["currentPreviewAfterStart"]["activation"]["rendererHandoff"])
        self.assertFalse(
            result["currentPreviewAfterStart"]["activation"]["gameplayTemplateExecution"]
        )
        self.assertFalse(result["currentPreviewAfterStart"]["activation"]["projectJsonModified"])
        self.assertFalse(result["currentPreviewAfterStart"]["activation"]["saveStateMutated"])
        self.assertFalse(result["currentPreviewAfterStart"]["activation"]["persisted"])
        self.assertEqual(
            result["summaryCurrentPreviewAfterStart"],
            result["currentPreviewAfterStart"],
        )
        self.assertTrue(result["activationStatusAfterStart"]["previewShellActive"])
        self.assertEqual(
            result["activationStatusAfterStart"]["currentPreviewScenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["purePreviewState"]["schema"],
            "game.generatedScenarioPreviewShellState.v1",
        )
        self.assertEqual(
            result["purePreviewState"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )

        self.assertEqual(
            result["purePreviewEvent"]["schema"],
            "game.generatedScenarioPreviewEvent.v1",
        )
        self.assertEqual(
            result["purePreviewEvent"]["kind"],
            "generated-scenario-preview-event",
        )
        self.assertEqual(result["purePreviewEvent"]["eventType"], "preview-created")
        self.assertEqual(
            result["purePreviewEvent"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["purePreviewEvent"]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertTrue(result["purePreviewEvent"]["runtimeLocal"])
        self.assertFalse(result["purePreviewEvent"]["persisted"])
        self.assertFalse(result["purePreviewEvent"]["rendererHandoff"])
        self.assertFalse(result["purePreviewEvent"]["gameplayTemplateExecution"])

        self.assertEqual(len(result["eventsAfterStart"]), 1)
        self.assertEqual(result["eventsAfterStart"], result["summaryEventsAfterStart"])
        self.assertEqual(result["eventsAfterStart"][0]["eventType"], "preview-created")
        self.assertEqual(
            result["eventsAfterStart"][0]["reason"],
            "generated-runtime-preview-shell-created",
        )
        self.assertEqual(
            result["eventsAfterStart"][0]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["eventsAfterStart"][0]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertTrue(result["eventsAfterStart"][0]["active"])
        self.assertTrue(result["eventsAfterStart"][0]["runtimeLocal"])
        self.assertFalse(result["eventsAfterStart"][0]["persisted"])
        self.assertEqual(result["activationStatusAfterStart"]["previewEventCount"], 1)
        self.assertEqual(result["disabledEventsAfterBlockedStart"], [])

        self.assertTrue(result["activationStatusBefore"]["enabled"])
        self.assertEqual(result["activationStatusBefore"]["mode"], "preview-shell")
        self.assertTrue(result["abortResult"]["cleared"])
        self.assertEqual(result["abortResult"]["reason"], "test-abort-generated-preview")
        self.assertEqual(
            result["abortResult"]["previous"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertIsNone(result["afterAbortPreview"])
        self.assertEqual(result["abortResult"]["event"]["eventType"], "preview-aborted")
        self.assertEqual(
            result["abortResult"]["event"]["reason"],
            "test-abort-generated-preview",
        )
        self.assertEqual(len(result["eventsAfterAbort"]), 2)
        self.assertEqual(result["eventsAfterAbort"][0]["eventType"], "preview-created")
        self.assertEqual(result["eventsAfterAbort"][1]["eventType"], "preview-aborted")
        self.assertFalse(result["eventsAfterAbort"][1]["active"])
        self.assertEqual(
            result["eventsAfterAbort"][1]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertTrue(result["acceptedAgain"]["accepted"])
        self.assertIsNotNone(result["currentPreviewBeforeToggleOff"])
        self.assertEqual(len(result["eventsAfterAcceptedAgain"]), 3)
        self.assertEqual(result["eventsAfterAcceptedAgain"][2]["eventType"], "preview-created")
        self.assertFalse(result["summaryActivation"]["enabled"])
        self.assertFalse(result["summaryActivation"]["previewShellActive"])
        self.assertEqual(result["summaryActivation"]["currentPreviewScenarioId"], "")
        self.assertEqual(result["toggledOff"]["mode"], "disabled")
        self.assertIsNone(result["previewAfterToggleOff"])
        self.assertEqual(len(result["eventsAfterToggleOff"]), 4)
        self.assertEqual(
            result["eventsAfterToggleOff"][3]["eventType"],
            "preview-cleared-activation-disabled",
        )
        self.assertEqual(
            result["eventsAfterToggleOff"][3]["reason"],
            "generated-activation-disabled",
        )
        self.assertFalse(result["blockedAfterToggle"]["accepted"])
        self.assertEqual(result["blockedAfterToggle"]["commandStatus"], "blocked")

        self.assertIsNone(result["generatedView"])
        self.assertIsNone(result["baseScenarioLookup"])
        self.assertIn("Unknown scenario", result["baseStartError"])
        self.assertEqual(result["scenarioCount"], 1)



    def test_generated_preview_shell_produces_template_handoff_contract_without_execution(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const generatedCatalog = {
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              exported: true,
              runtimeLoaded: false,
              projectJsonModified: false,
              activatedInRuntime: false,
              enabledPluginIds: ["plugin.hand-authored.vela-cave-extension.001"],
              entryPoints: ["scenario.plugin.vela-cave-extension.followup"],
              scenarioIds: ["scenario.plugin.vela-cave-extension.followup"],
              encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
              receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
              consequenceIds: ["consequence.plugin.vela-cave-extension.route-charted"],
              documents: [
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "scenario",
                  id: "scenario.plugin.vela-cave-extension.followup",
                  title: "Vela Cave Extension Followup",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/scenarios/vela-cave-extension-followup.json",
                  stageIds: ["route-opens", "escape-route", "extracted"],
                  encounterIds: ["encounter.plugin.vela-cave-extension.escape-route"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"]
                },
                {
                  pluginId: "plugin.hand-authored.vela-cave-extension.001",
                  kind: "encounter",
                  id: "encounter.plugin.vela-cave-extension.escape-route",
                  title: "Vela Cave Extension Escape Route",
                  path: "generated/gameplay-plugins/plugin.hand-authored.vela-cave-extension.001/content/encounters/vela-cave-extension-escape-route.json",
                  template: "encounter-template.cave-combat-run",
                  objectiveTypes: [
                    "objective-type.recover-item",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-extraction"
                  ],
                  actorArchetypes: ["actor-archetype.vela-cave-guard"],
                  receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
                  consequenceTypes: ["consequence-type.record-receipt"]
                }
              ],
              scenarios: [],
              encounters: [],
              plugins: [],
              problems: []
            };
            const runtime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-generated-template-handoff",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog,
              generatedScenarioActivationEnabled: true
            });
            const handoffBeforeStart = runtime.currentGeneratedScenarioTemplateHandoff();
            const accepted = runtime.startGeneratedScenario("scenario.plugin.vela-cave-extension.followup");
            const preview = runtime.currentGeneratedScenarioPreview();
            const handoff = runtime.currentGeneratedScenarioTemplateHandoff();
            const summaryHandoff = runtime.summary().currentGeneratedScenarioTemplateHandoff;
            const pureHandoff = scenarioApi.generatedScenarioTemplateHandoff(preview);
            const unsupportedPreview = {
              ...preview,
              primaryTemplateId: "encounter-template.unsupported"
            };
            const unsupportedHandoff = scenarioApi.generatedScenarioTemplateHandoff(unsupportedPreview);
            const inactiveHandoff = scenarioApi.generatedScenarioTemplateHandoff({
              ...preview,
              active: false,
              status: "aborted-preview-shell"
            });
            runtime.abortGeneratedScenarioPreview("template-handoff-test-abort");
            const handoffAfterAbort = runtime.currentGeneratedScenarioTemplateHandoff();
            console.log(JSON.stringify({
              accepted,
              preview,
              handoffBeforeStart,
              handoff,
              summaryHandoff,
              pureHandoff,
              unsupportedHandoff,
              inactiveHandoff,
              handoffAfterAbort,
              supportedTemplates: scenarioApi.GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES
            }));
            """
        )

        self.assertEqual(
            result["handoffBeforeStart"]["schema"],
            "game.generatedScenarioTemplateHandoff.v1",
        )
        self.assertEqual(
            result["handoffBeforeStart"]["kind"],
            "generated-scenario-template-handoff",
        )
        self.assertFalse(result["handoffBeforeStart"]["accepted"])
        self.assertTrue(result["handoffBeforeStart"]["blocked"])
        self.assertEqual(
            result["handoffBeforeStart"]["reason"],
            "missing-generated-preview-shell",
        )

        self.assertTrue(result["accepted"]["accepted"])
        self.assertIsNotNone(result["preview"])
        self.assertTrue(result["handoff"]["accepted"])
        self.assertFalse(result["handoff"]["blocked"])
        self.assertEqual(result["handoff"]["handoffStatus"], "ready")
        self.assertEqual(
            result["handoff"]["reason"],
            "generated-template-handoff-ready",
        )
        self.assertTrue(result["handoff"]["dryRun"])
        self.assertTrue(result["handoff"]["readOnly"])
        self.assertTrue(result["handoff"]["runtimeLocal"])
        self.assertFalse(result["handoff"]["persisted"])
        self.assertFalse(result["handoff"]["runtimeLoaded"])
        self.assertFalse(result["handoff"]["projectJsonModified"])
        self.assertFalse(result["handoff"]["activatedInRuntime"])
        self.assertFalse(result["handoff"]["rendererHandoff"])
        self.assertFalse(result["handoff"]["gameplayTemplateExecution"])
        self.assertFalse(result["handoff"]["saveStateMutated"])
        self.assertEqual(
            result["handoff"]["scenarioId"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["handoff"]["primaryEncounterId"],
            "encounter.plugin.vela-cave-extension.escape-route",
        )
        self.assertEqual(
            result["handoff"]["templateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertEqual(
            result["handoff"]["primaryTemplateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertTrue(result["handoff"]["supportedTemplate"])
        self.assertIn(
            "encounter-template.cave-combat-run",
            result["handoff"]["supportedTemplates"],
        )
        self.assertIn(
            "objective-type.clear-hostiles",
            result["handoff"]["objectiveTypes"],
        )
        self.assertIn(
            "actor-archetype.vela-cave-guard",
            result["handoff"]["actorArchetypes"],
        )
        self.assertIn(
            "receipt.plugin.vela-cave-extension.extracted",
            result["handoff"]["receiptIds"],
        )
        self.assertIn(
            "consequence-type.record-receipt",
            result["handoff"]["consequenceTypes"],
        )
        self.assertEqual(
            result["handoff"]["templateInput"]["scenario"]["id"],
            "scenario.plugin.vela-cave-extension.followup",
        )
        self.assertEqual(
            result["handoff"]["templateInput"]["encounter"]["templateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertIn(
            {"type": "objective-type.clear-hostiles"},
            result["handoff"]["templateInput"]["objectives"],
        )
        self.assertIn(
            {"archetype": "actor-archetype.vela-cave-guard"},
            result["handoff"]["templateInput"]["actors"],
        )
        self.assertEqual(
            result["handoff"]["operations"][0]["kind"],
            "handoff-generated-gameplay-template",
        )
        self.assertFalse(result["handoff"]["operations"][0]["executed"])
        self.assertFalse(result["handoff"]["activation"]["rendererHandoff"])
        self.assertFalse(result["handoff"]["activation"]["gameplayTemplateExecution"])
        self.assertFalse(result["handoff"]["activation"]["projectJsonModified"])
        self.assertFalse(result["handoff"]["activation"]["saveStateMutated"])
        self.assertFalse(result["handoff"]["activation"]["persisted"])

        self.assertEqual(result["summaryHandoff"], result["handoff"])
        self.assertEqual(result["pureHandoff"], result["handoff"])

        self.assertFalse(result["unsupportedHandoff"]["accepted"])
        self.assertTrue(result["unsupportedHandoff"]["blocked"])
        self.assertEqual(
            result["unsupportedHandoff"]["reason"],
            "unsupported-generated-gameplay-template",
        )
        self.assertFalse(result["unsupportedHandoff"]["supportedTemplate"])
        self.assertFalse(result["unsupportedHandoff"]["rendererHandoff"])
        self.assertFalse(result["unsupportedHandoff"]["gameplayTemplateExecution"])

        self.assertFalse(result["inactiveHandoff"]["accepted"])
        self.assertTrue(result["inactiveHandoff"]["blocked"])
        self.assertEqual(
            result["inactiveHandoff"]["reason"],
            "generated-preview-shell-inactive",
        )

        self.assertFalse(result["handoffAfterAbort"]["accepted"])
        self.assertTrue(result["handoffAfterAbort"]["blocked"])
        self.assertEqual(
            result["handoffAfterAbort"]["reason"],
            "missing-generated-preview-shell",
        )
        self.assertIn(
            "encounter-template.cave-combat-run",
            result["supportedTemplates"],
        )



    def test_generated_template_executor_registry_is_disabled_noop_by_default(self) -> None:
        result = self.run_node(
            r"""
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const runtime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-generated-template-executors",
              storage: null,
              restore: false
            });
            const registry = scenarioApi.generatedTemplateExecutorRegistry();
            const handoff = scenarioApi.generatedScenarioTemplateHandoff({
              kind: scenarioApi.GENERATED_SCENARIO_PREVIEW_SHELL_STATE_KIND,
              active: true,
              status: "active-preview-shell",
              scenarioId: "scenario.plugin.vela-cave-extension.followup",
              title: "Vela Cave Extension Followup",
              pluginId: "plugin.hand-authored.vela-cave-extension.001",
              primaryEncounterId: "encounter.plugin.vela-cave-extension.escape-route",
              primaryTemplateId: "encounter-template.cave-combat-run",
              objectiveTypes: [
                "objective-type.escape-captivity",
                "objective-type.clear-hostiles"
              ],
              actorArchetypes: ["actor-archetype.vela-cave-guard"],
              receiptIds: ["receipt.plugin.vela-cave-extension.extracted"],
              consequenceTypes: ["consequence-type.record-receipt"]
            });
            const status = scenarioApi.generatedTemplateExecutorStatus(handoff, {registry});
            const unsupportedStatus = scenarioApi.generatedTemplateExecutorStatus({
              accepted: true,
              scenarioId: "scenario.plugin.unsupported",
              title: "Unsupported Template",
              pluginId: "plugin.generated.unsupported",
              primaryEncounterId: "encounter.plugin.unsupported",
              templateId: "encounter-template.unsupported",
              primaryTemplateId: "encounter-template.unsupported",
              templateInput: {
                scenario: {id: "scenario.plugin.unsupported"},
                encounter: {
                  id: "encounter.plugin.unsupported",
                  templateId: "encounter-template.unsupported"
                }
              }
            }, {registry});
            console.log(JSON.stringify({
              registry,
              handoff,
              status,
              unsupportedStatus,
              missingPreviewStatus: runtime.currentGeneratedTemplateExecutorStatus(),
              summaryStatus: runtime.summary().currentGeneratedTemplateExecutorStatus,
              constants: {
                registrySchema: scenarioApi.GENERATED_TEMPLATE_EXECUTOR_REGISTRY_SCHEMA,
                registryKind: scenarioApi.GENERATED_TEMPLATE_EXECUTOR_REGISTRY_KIND,
                statusSchema: scenarioApi.GENERATED_TEMPLATE_EXECUTOR_STATUS_SCHEMA,
                statusKind: scenarioApi.GENERATED_TEMPLATE_EXECUTOR_STATUS_KIND,
                defaultMode: scenarioApi.GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE,
                supportedTemplates: scenarioApi.GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES
              }
            }));
            """
        )

        self.assertEqual(
            result["registry"]["schema"],
            result["constants"]["registrySchema"],
        )
        self.assertEqual(
            result["registry"]["kind"],
            result["constants"]["registryKind"],
        )
        self.assertEqual(result["registry"]["executorMode"], "disabled-no-op")
        self.assertFalse(result["registry"]["executionEnabled"])
        self.assertTrue(result["registry"]["noOp"])
        self.assertFalse(result["registry"]["rendererHandoff"])
        self.assertFalse(result["registry"]["gameplayTemplateExecution"])
        self.assertFalse(result["registry"]["saveStateMutated"])
        self.assertEqual(
            set(result["registry"]["registeredTemplateIds"]),
            set(result["constants"]["supportedTemplates"]),
        )
        self.assertIn(
            "encounter-template.cave-combat-run",
            result["registry"]["registeredTemplateIds"],
        )
        for executor in result["registry"]["executors"]:
            self.assertTrue(executor["registered"])
            self.assertEqual(executor["executorMode"], "disabled-no-op")
            self.assertFalse(executor["executionEnabled"])
            self.assertTrue(executor["noOp"])
            self.assertFalse(executor["rendererHandoff"])
            self.assertFalse(executor["gameplayTemplateExecution"])

        self.assertTrue(result["handoff"]["accepted"])
        self.assertEqual(result["handoff"]["templateId"], "encounter-template.cave-combat-run")

        self.assertEqual(
            result["status"]["schema"],
            result["constants"]["statusSchema"],
        )
        self.assertEqual(result["status"]["kind"], result["constants"]["statusKind"])
        self.assertFalse(result["status"]["accepted"])
        self.assertTrue(result["status"]["blocked"])
        self.assertTrue(result["status"]["handoffAccepted"])
        self.assertTrue(result["status"]["executorRegistered"])
        self.assertFalse(result["status"]["executionEnabled"])
        self.assertEqual(result["status"]["executionStatus"], "disabled-no-op")
        self.assertEqual(result["status"]["reason"], "generated-template-execution-disabled")
        self.assertTrue(result["status"]["wouldExecuteIfEnabled"])
        self.assertFalse(result["status"]["executed"])
        self.assertFalse(result["status"]["rendererHandoff"])
        self.assertFalse(result["status"]["gameplayTemplateExecution"])
        self.assertFalse(result["status"]["saveStateMutated"])
        self.assertEqual(
            result["status"]["executor"]["templateId"],
            "encounter-template.cave-combat-run",
        )
        self.assertEqual(
            result["status"]["operations"][0]["kind"],
            "preview-generated-gameplay-template-executor",
        )
        self.assertTrue(result["status"]["operations"][0]["wouldExecuteIfEnabled"])
        self.assertFalse(result["status"]["operations"][0]["executed"])
        self.assertFalse(result["status"]["operations"][0]["rendererHandoff"])
        self.assertFalse(result["status"]["operations"][0]["gameplayTemplateExecution"])

        self.assertFalse(result["unsupportedStatus"]["accepted"])
        self.assertTrue(result["unsupportedStatus"]["blocked"])
        self.assertFalse(result["unsupportedStatus"]["executorRegistered"])
        self.assertEqual(
            result["unsupportedStatus"]["reason"],
            "no-generated-template-executor",
        )
        self.assertEqual(result["unsupportedStatus"]["operations"], [])

        self.assertFalse(result["missingPreviewStatus"]["accepted"])
        self.assertTrue(result["missingPreviewStatus"]["blocked"])
        self.assertEqual(
            result["missingPreviewStatus"]["reason"],
            "generated-template-handoff-blocked",
        )
        self.assertEqual(result["summaryStatus"], result["missingPreviewStatus"])


    def test_generated_shuttle_ambush_executor_preview_contract_is_preview_only(self) -> None:
        result = self.run_node(
            r"""
            const scenarioApi = require(process.argv[1]);
            const registry = scenarioApi.generatedTemplateExecutorRegistry();
            const handoff = scenarioApi.generatedScenarioTemplateHandoff({
              kind: scenarioApi.GENERATED_SCENARIO_PREVIEW_SHELL_STATE_KIND,
              active: true,
              status: "active-preview-shell",
              scenarioId: "scenario.plugin.opening-shuttle-ambush.elite-wave",
              title: "Opening Shuttle Ambush Elite Wave",
              pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
              primaryEncounterId: "encounter.plugin.opening-shuttle-ambush.elite-wave",
              primaryTemplateId: "encounter-template.shuttle-ambush",
              objectiveTypes: [
                "objective-type.survive",
                "objective-type.clear-hostiles",
                "objective-type.reach-destination"
              ],
              actorArchetypes: [
                "actor-archetype.shuttle-raider",
                "actor-archetype.ship-security"
              ],
              receiptIds: ["receipt.plugin.opening-shuttle-ambush.cleared"],
              consequenceTypes: [
                "consequence-type.record-receipt",
                "consequence-type.mark-system"
              ]
            });
            const status = scenarioApi.generatedTemplateExecutorStatus(handoff, {registry});
            console.log(JSON.stringify({handoff, status}));
            """
        )

        self.assertTrue(result["handoff"]["accepted"])
        self.assertEqual(
            result["handoff"]["templateId"],
            "encounter-template.shuttle-ambush",
        )

        status = result["status"]
        self.assertFalse(status["accepted"])
        self.assertTrue(status["blocked"])
        self.assertTrue(status["handoffAccepted"])
        self.assertTrue(status["executorRegistered"])
        self.assertFalse(status["executionEnabled"])
        self.assertEqual(status["reason"], "generated-template-execution-disabled")
        self.assertEqual(status["executionStatus"], "disabled-no-op")
        self.assertFalse(status["rendererHandoff"])
        self.assertFalse(status["gameplayTemplateExecution"])
        self.assertFalse(status["saveStateMutated"])
        self.assertFalse(status["executed"])
        self.assertEqual(
            status["operations"][0]["kind"],
            "preview-generated-gameplay-template-executor",
        )
        self.assertFalse(status["operations"][0]["executed"])
        self.assertFalse(status["operations"][0]["rendererHandoff"])
        self.assertFalse(status["operations"][0]["gameplayTemplateExecution"])

        preview = status["templatePreview"]
        self.assertEqual(status["previewContract"], preview)
        self.assertEqual(
            preview["schema"],
            "game.generatedTemplateExecutorPreview.shuttleAmbush.v1",
        )
        self.assertEqual(preview["kind"], "generated-template-executor-preview")
        self.assertEqual(preview["previewKind"], "shuttle-ambush-plan")
        self.assertEqual(preview["templateId"], "encounter-template.shuttle-ambush")
        self.assertTrue(preview["readOnly"])
        self.assertTrue(preview["dryRun"])
        self.assertTrue(preview["runtimeLocal"])
        self.assertTrue(preview["noOp"])
        self.assertFalse(preview["executionEnabled"])
        self.assertFalse(preview["executed"])
        self.assertFalse(preview["rendererHandoff"])
        self.assertFalse(preview["gameplayTemplateExecution"])
        self.assertFalse(preview["saveStateMutated"])
        self.assertFalse(preview["projectJsonModified"])
        self.assertFalse(preview["persisted"])

        self.assertEqual(
            preview["scenario"]["id"],
            "scenario.plugin.opening-shuttle-ambush.elite-wave",
        )
        self.assertEqual(
            preview["encounter"]["id"],
            "encounter.plugin.opening-shuttle-ambush.elite-wave",
        )
        self.assertEqual(
            preview["encounter"]["templateId"],
            "encounter-template.shuttle-ambush",
        )
        self.assertEqual(
            [objective["type"] for objective in preview["objectiveSequence"]],
            [
                "objective-type.survive",
                "objective-type.clear-hostiles",
                "objective-type.reach-destination",
            ],
        )
        self.assertTrue(preview["survivalRequired"])
        self.assertTrue(preview["clearHostilesRequired"])
        self.assertTrue(preview["destinationRequired"])
        self.assertEqual(len(preview["waves"]), 1)
        self.assertEqual(preview["waves"][0]["trigger"], "encounter-start")
        self.assertEqual(
            preview["waves"][0]["hostileArchetypeId"],
            "actor-archetype.shuttle-raider",
        )
        self.assertEqual(preview["waves"][0]["spawnPointIds"], [])
        self.assertEqual(
            preview["waves"][0]["spawnPointContract"],
            "opening-shuttle-ambush-runtime-selects-safe-boarder-pads",
        )
        self.assertEqual(
            preview["hostiles"],
            [
                {
                    "role": "hostile",
                    "actorArchetypeId": "actor-archetype.shuttle-raider",
                    "count": 1,
                }
            ],
        )
        self.assertEqual(
            preview["completion"]["receiptIds"],
            ["receipt.plugin.opening-shuttle-ambush.cleared"],
        )
        self.assertEqual(
            preview["completion"]["consequenceTypes"],
            ["consequence-type.record-receipt", "consequence-type.mark-system"],
        )
        self.assertIn(
            "opening-shuttle-hostiles-cleared",
            preview["completion"]["requiredSignals"],
        )
        self.assertFalse(preview["execution"]["rendererHandoff"])
        self.assertFalse(preview["execution"]["gameplayTemplateExecution"])
        self.assertFalse(preview["execution"]["saveStateMutated"])
        self.assertFalse(preview["operations"][0]["executed"])
        self.assertFalse(preview["operations"][0]["rendererHandoff"])
        self.assertFalse(preview["operations"][0]["gameplayTemplateExecution"])





    def test_active_gameplay_pack_selection_builds_opening_shuttle_config_or_none(self) -> None:
        result = self.run_node(
            r'''
            const fs = require("fs");
            const scenarioApi = require(process.argv[1]);
            const project = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
            const generatedCatalog = {
              schema: scenarioApi.GENERATED_GAMEPLAY_CATALOG_SCHEMA,
              kind: scenarioApi.GENERATED_GAMEPLAY_CATALOG_KIND,
              status: "ready",
              runtimeStatus: scenarioApi.GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
              exported: true,
              runtimeLoaded: false,
              projectJsonModified: false,
              activatedInRuntime: false,
              enabledPluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
              entryPoints: ["scenario.plugin.opening-shuttle-ambush.elite-wave"],
              scenarioIds: ["scenario.plugin.opening-shuttle-ambush.elite-wave"],
              encounterIds: ["encounter.plugin.opening-shuttle-ambush.elite-wave"],
              receiptIds: ["receipt.plugin.opening-shuttle-ambush.cleared"],
              consequenceIds: ["consequence.plugin.opening-shuttle-ambush.system-marked"],
              documents: [
                {
                  pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                  kind: "scenario",
                  id: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                  title: "Opening Shuttle Ambush Elite Wave",
                  path: "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/scenarios/opening-shuttle-ambush-elite-wave.json",
                  stageIds: ["boarding", "cleared"],
                  encounterIds: ["encounter.plugin.opening-shuttle-ambush.elite-wave"],
                  receiptIds: ["receipt.plugin.opening-shuttle-ambush.cleared"],
                  consequenceTypes: ["consequence-type.record-receipt", "consequence-type.mark-system"]
                },
                {
                  pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                  kind: "encounter",
                  id: "encounter.plugin.opening-shuttle-ambush.elite-wave",
                  title: "Opening Shuttle Ambush Elite Wave",
                  path: "generated/gameplay-plugins/plugin.hand-authored.opening-shuttle-ambush.001/content/encounters/opening-shuttle-ambush-elite-wave.json",
                  template: "encounter-template.shuttle-ambush",
                  objectiveTypes: [
                    "objective-type.survive",
                    "objective-type.clear-hostiles",
                    "objective-type.reach-destination"
                  ],
                  objectives: [
                    {id: "survive-boarding", type: "objective-type.survive", required: true},
                    {id: "clear-raiders", type: "objective-type.clear-hostiles", required: true},
                    {id: "reach-haven-orbit", type: "objective-type.reach-destination", required: true}
                  ],
                  actorArchetypes: ["actor-archetype.shuttle-raider"],
                  participants: [
                    {
                      role: "hostile",
                      actorArchetypeId: "actor-archetype.shuttle-raider",
                      count: 3
                    }
                  ],
                  location: {
                    systemId: "system.solace-reach",
                    destinationId: "destination.solace-reach.haven-orbit"
                  },
                  receiptIds: ["receipt.plugin.opening-shuttle-ambush.cleared"],
                  consequenceTypes: ["consequence-type.record-receipt", "consequence-type.mark-system"]
                }
              ],
              scenarios: [],
              encounters: [],
              plugins: [],
              problems: []
            };
            const noneRuntime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-none",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog,
              activeGameplayPackIds: []
            });
            const packRuntime = scenarioApi.create(project.metadata.systemScenarios, {
              projectId: "webgl-demo-pack",
              storage: null,
              restore: false,
              generatedGameplayCatalog: generatedCatalog,
              activeGameplayPackIds: ["plugin.hand-authored.opening-shuttle-ambush.001"]
            });
            const resetToNone = packRuntime.setActiveGameplayPackIds("None");
            const selectedAgain = packRuntime.setActiveGameplayPackIds(
              "plugin.hand-authored.opening-shuttle-ambush.001"
            );
            console.log(JSON.stringify({
              noneSelection: noneRuntime.activeGameplayPackSelection(),
              noneConfig: noneRuntime.openingShuttleGameplayPackConfig({baseHostileCount: 2}),
              selectedAgain,
              resetToNone,
              packSelection: packRuntime.activeGameplayPackSelection(),
              packConfig: packRuntime.openingShuttleGameplayPackConfig({baseHostileCount: 2}),
              summary: packRuntime.summary()
            }));
            '''
        )

        assert result["noneSelection"]["schema"] == "game.activeGameplayPackSelection.v1"
        assert result["noneSelection"]["mode"] == "none"
        assert result["noneSelection"]["activePluginIds"] == []
        assert result["noneConfig"]["schema"] == "game.openingShuttleGameplayPackConfig.v1"
        assert result["noneConfig"]["available"] is False
        assert result["noneConfig"]["active"] is False
        assert result["noneConfig"]["reason"] == "no-active-gameplay-pack-selected"
        assert result["noneConfig"]["extraHostileCount"] == 0
        assert result["noneConfig"]["eliteWave"]["enabled"] is False

        assert result["resetToNone"]["mode"] == "none"
        assert result["resetToNone"]["activePluginIds"] == []
        assert result["selectedAgain"]["mode"] == "selected"
        assert result["selectedAgain"]["activePluginIds"] == [
            "plugin.hand-authored.opening-shuttle-ambush.001"
        ]

        pack_config = result["packConfig"]
        assert pack_config["available"] is True
        assert pack_config["active"] is True
        assert pack_config["pluginId"] == "plugin.hand-authored.opening-shuttle-ambush.001"
        assert pack_config["scenarioId"] == "scenario.plugin.opening-shuttle-ambush.elite-wave"
        assert pack_config["encounterId"] == "encounter.plugin.opening-shuttle-ambush.elite-wave"
        assert pack_config["templateId"] == "encounter-template.shuttle-ambush"
        assert pack_config["systemId"] == "system.solace-reach"
        assert pack_config["destinationId"] == "destination.solace-reach.haven-orbit"
        assert pack_config["baseHostileCount"] == 2
        assert pack_config["hostileCount"] == 3
        assert pack_config["extraHostileCount"] == 1
        assert pack_config["eliteWave"] == {
            "enabled": True,
            "triggerDefeats": 2,
            "count": 1,
            "actorArchetypeId": "actor-archetype.shuttle-raider",
            "source": "plugin.hand-authored.opening-shuttle-ambush.001",
            "scenarioId": "scenario.plugin.opening-shuttle-ambush.elite-wave",
            "encounterId": "encounter.plugin.opening-shuttle-ambush.elite-wave",
            "displayName": "Elite Boarding Leader",
            "objectiveLabel": "Defeat the elite boarding leader",
            "alert": "Opening Shuttle Ambush Elite Wave: elite boarding leader inbound — 3x hostile health confirmed",
            "healthMultiplier": 3,
        }
        assert pack_config["generatedPluginExecution"] is False
        assert pack_config["generatedTemplateExecution"] is False
        assert pack_config["rendererHandoff"] is False
        assert pack_config["saveStateMutated"] is False
        assert pack_config["projectJsonModified"] is False
        assert result["summary"]["activeGameplayPackSelection"]["mode"] == "selected"
        assert result["summary"]["openingShuttleGameplayPackConfig"]["extraHostileCount"] == 1


    def test_webgl_desktop_exposes_reload_time_gameplay_pack_selector(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
        css = GAME_EDITOR_CSS.read_text(encoding="utf-8")

        self.assertIn('data-webgl-gameplay-pack-controls', applications)
        self.assertIn('id="webgl-gameplay-pack-select"', applications)
        self.assertIn('id="webgl-gameplay-pack-apply"', applications)
        self.assertIn("None — base game", applications)

        self.assertIn("WEBGL_GAMEPLAY_PACK_STORAGE_KEY", desktop)
        self.assertIn("webglReadStoredGameplayPackSelection", desktop)
        self.assertIn("syncWebglGameplayPackControls", desktop)
        self.assertIn("applyWebglGameplayPackControlSelection", desktop)
        self.assertIn("reloadGameplayPackSelection: webglReloadGameplayPackSelection", desktop)
        self.assertIn("syncGameplayPackControls: syncWebglGameplayPackControls", desktop)
        self.assertIn(".webgl-gameplay-pack-controls", css)

    def test_webgl_desktop_pack_selection_none_overrides_metadata_until_query_override(self) -> None:
        result = self.run_webgl_desktop_node(
            r'''
            const fs = require("fs");
            const vm = require("vm");
            const storage = new Map();
            const windowObj = {
              localStorage: {
                getItem(key) { return storage.has(key) ? storage.get(key) : null; },
                setItem(key, value) { storage.set(key, String(value)); }
              },
              location: {search: "", reload() {}},
              addEventListener() {}
            };
            const context = {
              window: windowObj,
              document: {
                querySelector() { return null; },
                querySelectorAll() { return []; },
                createElement() { return {appendChild() {}, addEventListener() {}, dataset: {}}; }
              },
              console,
              URLSearchParams,
              ensureDesktopIcons() {},
              currentApp: "webgl",
              setActiveApp() {},
              HTMLAnchorElement: class {},
              HTMLElement: class {}
            };
            vm.createContext(context);
            vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
            const api = windowObj.MainComputerWebglSystemScenario;
            const project = {
              metadata: {
                activeGameplayPackIds: ["plugin.hand-authored.opening-shuttle-ambush.001"]
              }
            };
            const metadataDefault = api.reloadGameplayPackSelection(project);
            storage.set(
              "main-computer.webgl.active-gameplay-packs.v1",
              JSON.stringify(["None"])
            );
            const storedNone = api.reloadGameplayPackSelection(project);
            windowObj.location.search = "?gameplayPack=plugin.hand-authored.opening-shuttle-ambush.001";
            const queryOverride = api.reloadGameplayPackSelection(project);
            console.log(JSON.stringify({metadataDefault, storedNone, queryOverride}));
            '''
        )

        self.assertEqual(result["metadataDefault"]["mode"], "selected")
        self.assertEqual(
            result["metadataDefault"]["activeGameplayPackIds"],
            ["pack.opening-shuttle.elite-boarders"],
        )
        self.assertEqual(result["storedNone"]["mode"], "none")
        self.assertEqual(result["storedNone"]["activeGameplayPackIds"], [])
        self.assertEqual(result["storedNone"]["source"], "local-storage")
        self.assertEqual(result["queryOverride"]["mode"], "selected")
        self.assertEqual(result["queryOverride"]["source"], "query-param")

    def test_webgl_desktop_pack_selector_lists_none_and_available_packs(self) -> None:
        result = self.run_webgl_desktop_node(
            r'''
            const fs = require("fs");
            const vm = require("vm");
            const storage = new Map();
            let reloadCount = 0;
            const nodes = {
              controls: {dataset: {}},
              select: {
                children: [],
                value: "",
                disabled: false,
                textContent: "",
                appendChild(option) { this.children.push(option); },
                addEventListener() {}
              },
              apply: {addEventListener() {}},
              status: {textContent: ""}
            };
            const windowObj = {
              localStorage: {
                getItem(key) { return storage.has(key) ? storage.get(key) : null; },
                setItem(key, value) { storage.set(key, String(value)); }
              },
              location: {search: "", reload() { reloadCount += 1; }},
              addEventListener() {}
            };
            const documentObj = {
              querySelector(selector) {
                if (selector === "[data-webgl-gameplay-pack-controls]") return nodes.controls;
                if (selector === "#webgl-gameplay-pack-select") return nodes.select;
                if (selector === "#webgl-gameplay-pack-apply") return nodes.apply;
                if (selector === "#webgl-gameplay-pack-status") return nodes.status;
                return null;
              },
              querySelectorAll() { return []; },
              createElement(tag) {
                return {
                  tagName: String(tag || "").toUpperCase(),
                  value: "",
                  textContent: "",
                  dataset: {},
                  appendChild() {},
                  addEventListener() {}
                };
              }
            };
            const context = {
              window: windowObj,
              document: documentObj,
              console,
              URLSearchParams,
              ensureDesktopIcons() {},
              currentApp: "webgl",
              setActiveApp() {},
              HTMLAnchorElement: class {},
              HTMLElement: class {}
            };
            vm.createContext(context);
            vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
            const api = windowObj.MainComputerWebglSystemScenario;
            const project = {
              metadata: {
                generatedGameplayPlugins: {
                  enabledPluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                  documents: [
                    {
                      pluginId: "plugin.hand-authored.opening-shuttle-ambush.001",
                      kind: "scenario",
                      id: "scenario.plugin.opening-shuttle-ambush.elite-wave",
                      title: "Opening Shuttle Ambush Elite Wave"
                    }
                  ]
                }
              }
            };
            const runtime = {
              activeGameplayPackSelection() {
                return {
                  mode: "selected",
                  activePluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                  availablePluginIds: ["plugin.hand-authored.opening-shuttle-ambush.001"],
                  missingPluginIds: []
                };
              }
            };
            const state = api.syncGameplayPackControls(project, runtime);
            nodes.select.value = "None";
            const applied = api.applyGameplayPackControlSelection();
            console.log(JSON.stringify({
              state,
              options: nodes.select.children.map((option) => ({
                value: option.value,
                text: option.textContent
              })),
              selected: nodes.select.value,
              disabled: nodes.select.disabled,
              controlsDataset: nodes.controls.dataset,
              statusText: nodes.status.textContent,
              stored: storage.get("main-computer.webgl.active-gameplay-packs.v1"),
              applied,
              reloadCount
            }));
            '''
        )

        self.assertEqual(result["state"]["mode"], "selected")
        self.assertEqual(
            result["state"]["activeGameplayPackIds"],
            ["pack.opening-shuttle.elite-boarders"],
        )
        self.assertEqual(
            [option["value"] for option in result["options"]],
            ["None", "pack.opening-shuttle.elite-boarders", "pack.main-ship.bay-boarders"],
        )
        self.assertIn("None — base game", result["options"][0]["text"])
        self.assertIn("Opening Shuttle: Elite Boarders", result["options"][1]["text"])
        self.assertIn("Main Ship: Bay Boarders", result["options"][2]["text"])
        self.assertFalse(result["disabled"])
        self.assertEqual(result["controlsDataset"]["gameplayPackMode"], "selected")
        self.assertEqual(json.loads(result["stored"]), {"schema": "game.reloadGameplayPackSelection.v1", "mode": "none", "activeGameplayPackIds": []})
        self.assertEqual(result["applied"]["mode"], "none")
        self.assertEqual(result["reloadCount"], 1)

    def test_webgl_desktop_passes_project_generated_catalog_to_system_scenario_runtime(self) -> None:
        desktop = WEBGL_DESKTOP.read_text(encoding="utf-8")
        self.assertIn(
            "generatedGameplayCatalog: project?.metadata?.generatedGameplayPlugins",
            desktop,
        )
        self.assertIn("webglReloadGameplayPackSelection(project)", desktop)
        self.assertIn("activeGameplayPackIds: packSelection.activeGameplayPackIds", desktop)
        self.assertIn('WEBGL_GAMEPLAY_PACK_STORAGE_KEY', desktop)
        self.assertIn('params.has("gameplayPack")', desktop)
        self.assertIn('params.has("gameplayPacks")', desktop)


if __name__ == "__main__":
    unittest.main()
