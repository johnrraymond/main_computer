from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENCOUNTER_STATE = (
    ROOT
    / "main_computer"
    / "web"
    / "applications"
    / "scripts"
    / "encounter-state.js"
)


class EncounterStateModuleTests(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is required for encounter-state module tests")
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(script), str(ENCOUNTER_STATE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_actor_group_boundaries_cover_unavailable_missing_and_mixed(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const unavailable = encounter.classifyActorGroup(null);
            const missing = encounter.classifyActorGroup({
              actorIds: ["enemy.one", "enemy.two"],
              snapshot: {characters: {}}
            });
            const mixed = encounter.classifyActorGroup({
              actorIds: ["enemy.one", "enemy.two", "enemy.three", "enemy.one"],
              snapshot: {
                actors: {
                  "enemy.one": {status: "active", health: 10},
                  "enemy.two": {status: "down", health: 0}
                }
              }
            });

            console.log(JSON.stringify({
              unavailableStatus: unavailable.status,
              unavailableTotal: unavailable.total,
              missingStatus: missing.status,
              missingCount: missing.missingCount,
              mixedStatus: mixed.status,
              mixedTotal: mixed.total,
              mixedActiveCount: mixed.activeCount,
              mixedDefeatedCount: mixed.defeatedCount,
              mixedMissingCount: mixed.missingCount,
              duplicateCollapsed: mixed.actors.length
            }));
            """
        )

        self.assertEqual(result["unavailableStatus"], "unavailable")
        self.assertEqual(result["unavailableTotal"], 0)
        self.assertEqual(result["missingStatus"], "missing")
        self.assertEqual(result["missingCount"], 2)
        self.assertEqual(result["mixedStatus"], "mixed")
        self.assertEqual(result["mixedTotal"], 3)
        self.assertEqual(result["mixedActiveCount"], 1)
        self.assertEqual(result["mixedDefeatedCount"], 1)
        self.assertEqual(result["mixedMissingCount"], 1)
        self.assertEqual(result["duplicateCollapsed"], 3)

    def test_staged_encounter_classification_has_explicit_boundaries(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const activeActors = {
              status: encounter.ACTOR_GROUP_STATUS.active,
              total: 2,
              activeCount: 2,
              defeatedCount: 0,
              missingCount: 0,
              actors: []
            };
            const defeatedActors = {
              status: encounter.ACTOR_GROUP_STATUS.defeated,
              total: 2,
              activeCount: 0,
              defeatedCount: 2,
              missingCount: 0,
              actors: []
            };

            const inactive = encounter.classifyStagedEncounterState({
              view: {visible: false, state: {status: "active", stageId: "combat"}},
              actorGroup: activeActors,
              activeStageId: "combat",
              completedStageId: "investigation"
            });
            const actorUnavailable = encounter.classifyStagedEncounterState({
              view: {visible: true, state: {status: "active", stageId: "combat"}},
              actorGroup: {status: encounter.ACTOR_GROUP_STATUS.unavailable},
              activeStageId: "combat",
              completedStageId: "investigation"
            });
            const outside = encounter.classifyStagedEncounterState({
              view: {visible: true, state: {status: "active", stageId: "briefing"}},
              actorGroup: activeActors,
              activeStageId: "combat",
              completedStageId: "investigation"
            });
            const completedActive = encounter.classifyStagedEncounterState({
              view: {visible: true, state: {status: "active", stageId: "investigation"}},
              actorGroup: activeActors,
              activeStageId: "combat",
              completedStageId: "investigation",
              stateLabels: {
                recoverableCompletedActive: "custom-completed-active"
              },
              recoveryActions: {
                restartEncounter: "custom-restart"
              }
            });
            const completedDefeated = encounter.classifyStagedEncounterState({
              view: {visible: true, state: {status: "active", stageId: "investigation"}},
              actorGroup: defeatedActors,
              activeStageId: "combat",
              completedStageId: "investigation"
            });

            console.log(JSON.stringify({
              inactiveStatus: inactive.status,
              inactiveStageClass: inactive.stageClass,
              actorUnavailableStatus: actorUnavailable.status,
              actorUnavailableStageClass: actorUnavailable.stageClass,
              outsideStatus: outside.status,
              outsideStageClass: outside.stageClass,
              completedActiveStatus: completedActive.status,
              completedActiveRecovery: completedActive.recovery,
              completedDefeatedStatus: completedDefeated.status,
              completedDefeatedRecovery: completedDefeated.recovery
            }));
            """
        )

        self.assertEqual(result["inactiveStatus"], "scenario-inactive")
        self.assertEqual(result["inactiveStageClass"], "inactive")
        self.assertEqual(result["actorUnavailableStatus"], "actor-runtime-unavailable")
        self.assertEqual(result["actorUnavailableStageClass"], "actor-runtime-unavailable")
        self.assertEqual(result["outsideStatus"], "outside-encounter")
        self.assertEqual(result["outsideStageClass"], "outside")
        self.assertEqual(result["completedActiveStatus"], "custom-completed-active")
        self.assertEqual(result["completedActiveRecovery"], "custom-restart")
        self.assertEqual(result["completedDefeatedStatus"], "recoverable-completed-defeated")
        self.assertEqual(result["completedDefeatedRecovery"], "restart-encounter")

    def test_reconciliation_and_diagnostics_are_policy_driven(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const activeRecovery = {
              status: "custom-active",
              recovery: "custom-restart",
              actorGroup: {status: encounter.ACTOR_GROUP_STATUS.active}
            };
            const defeatedRecovery = {
              status: "custom-defeated",
              recovery: "custom-restart",
              actorGroup: {status: encounter.ACTOR_GROUP_STATUS.defeated}
            };
            const activePlan = encounter.reconciliationPlan(activeRecovery, {
              recoveryActions: {restartEncounter: "custom-restart"}
            });
            const blockedDefeatedPlan = encounter.reconciliationPlan(defeatedRecovery, {
              recoveryActions: {restartEncounter: "custom-restart"},
              recoverDefeated: false
            });
            const allowedDefeatedPlan = encounter.reconciliationPlan(defeatedRecovery, {
              recoveryActions: {restartEncounter: "custom-restart"},
              recoverDefeated: true
            });
            const emptyPlan = encounter.reconciliationPlan(null);
            const emptyDiagnostic = encounter.diagnosticSnapshot(null, emptyPlan);

            console.log(JSON.stringify({
              activeRecover: activePlan.recover,
              activeAction: activePlan.action,
              blockedDefeatedRecover: blockedDefeatedPlan.recover,
              allowedDefeatedRecover: allowedDefeatedPlan.recover,
              emptyPlanRecover: emptyPlan.recover,
              emptyPlanReason: emptyPlan.reason,
              emptyDiagnosticStatus: emptyDiagnostic.status,
              emptyDiagnosticPlanAction: emptyDiagnostic.plan.action,
              customSuccess: encounter.recoverySucceeded(
                {action: "custom-restart"},
                {resetApplied: true},
                {successKeys: {"custom-restart": "resetApplied"}}
              ),
              missingSuccess: encounter.recoverySucceeded(
                {action: "custom-restart"},
                null,
                {successKeys: {"custom-restart": "resetApplied"}}
              )
            }));
            """
        )

        self.assertTrue(result["activeRecover"])
        self.assertEqual(result["activeAction"], "custom-restart")
        self.assertFalse(result["blockedDefeatedRecover"])
        self.assertTrue(result["allowedDefeatedRecover"])
        self.assertFalse(result["emptyPlanRecover"])
        self.assertEqual(result["emptyPlanReason"], "unavailable")
        self.assertEqual(result["emptyDiagnosticStatus"], "unavailable")
        self.assertEqual(result["emptyDiagnosticPlanAction"], "none")
        self.assertTrue(result["customSuccess"])
        self.assertFalse(result["missingSuccess"])

    def test_encounter_identity_flows_into_classification_and_diagnostic(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const classification = encounter.classifyStagedEncounterState({
              key: "scenario.alpha:encounter.boarding-defense",
              definitionId: "encounter.boarding-defense",
              scenarioId: "scenario.alpha",
              systemId: "system.alpha",
              view: {visible: true, state: {status: "active", stageId: "combat"}},
              actorGroup: {
                status: encounter.ACTOR_GROUP_STATUS.active,
                total: 2,
                activeCount: 2,
                defeatedCount: 0,
                missingCount: 0,
                actors: []
              },
              actorIds: ["enemy.one", "enemy.two", "enemy.one"],
              activeStageIds: ["combat"],
              completedStageIds: ["investigation"]
            });
            const plan = encounter.reconciliationPlan(classification);
            const diagnostic = encounter.diagnosticSnapshot(classification, plan);
            const explicit = encounter.encounterIdentity({
              key: "scenario.alpha:encounter.boarding-defense",
              definitionId: "encounter.boarding-defense",
              instanceId: "run-7",
              scenarioId: "scenario.alpha",
              systemId: "system.alpha",
              stageId: "combat",
              activeStageIds: ["combat"],
              completedStageIds: ["investigation"],
              actorIds: ["enemy.one", "enemy.two"]
            });

            console.log(JSON.stringify({
              classificationIdentity: classification.identity,
              diagnosticEncounter: diagnostic.encounter,
              explicit
            }));
            """
        )

        classified = result["classificationIdentity"]
        diagnostic = result["diagnosticEncounter"]
        explicit = result["explicit"]

        self.assertEqual(classified["key"], "scenario.alpha:encounter.boarding-defense")
        self.assertEqual(classified["definitionId"], "encounter.boarding-defense")
        self.assertIsNone(classified["instanceId"])
        self.assertFalse(classified["instanceKnown"])
        self.assertEqual(classified["scenarioId"], "scenario.alpha")
        self.assertEqual(classified["systemId"], "system.alpha")
        self.assertEqual(classified["stageId"], "combat")
        self.assertEqual(classified["activeStageIds"], ["combat"])
        self.assertEqual(classified["completedStageIds"], ["investigation"])
        self.assertEqual(classified["actorIds"], ["enemy.one", "enemy.two"])
        self.assertEqual(diagnostic, classified)
        self.assertEqual(explicit["instanceId"], "run-7")
        self.assertTrue(explicit["instanceKnown"])


    def test_encounter_instance_descriptor_provides_stable_placeholder_without_persistence(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const classification = encounter.classifyStagedEncounterState({
              key: "scenario.alpha:encounter.boarding-defense",
              definitionId: "encounter.boarding-defense",
              scenarioId: "scenario.alpha",
              systemId: "system.alpha",
              view: {visible: true, state: {status: "active", stageId: "combat"}},
              actorGroup: {
                status: encounter.ACTOR_GROUP_STATUS.active,
                total: 2,
                activeCount: 2,
                defeatedCount: 0,
                missingCount: 0,
                actors: []
              },
              actorIds: ["enemy.one", "enemy.two"],
              activeStageIds: ["combat"],
              completedStageIds: ["investigation"],
              instanceSource: "test-diagnostic-placeholder"
            });
            const diagnostic = encounter.diagnosticSnapshot(
              classification,
              encounter.reconciliationPlan(classification),
              {instanceSource: "test-diagnostic-placeholder"}
            );
            const directPlaceholder = encounter.encounterInstanceDescriptor({
              identity: classification.identity,
              source: "test-diagnostic-placeholder"
            });
            const known = encounter.encounterInstanceDescriptor({
              identity: classification.identity,
              instanceId: "encounter-run-7"
            });

            console.log(JSON.stringify({
              classificationInstance: classification.instance,
              diagnosticInstance: diagnostic.instance,
              directPlaceholder,
              known,
              exportedStatus: encounter.ENCOUNTER_INSTANCE_STATUS
            }));
            """
        )

        expected_key = (
            "scenario.alpha:encounter.boarding-defense"
            ":instance:combat:pending"
        )
        classification_instance = result["classificationInstance"]
        diagnostic_instance = result["diagnosticInstance"]
        direct_placeholder = result["directPlaceholder"]
        known = result["known"]

        self.assertEqual(result["exportedStatus"]["placeholder"], "placeholder")
        self.assertEqual(result["exportedStatus"]["known"], "known")
        self.assertEqual(classification_instance["status"], "placeholder")
        self.assertEqual(classification_instance["proposedInstanceId"], expected_key)
        self.assertEqual(classification_instance["proposedInstanceKey"], expected_key)
        self.assertTrue(classification_instance["placeholder"])
        self.assertFalse(classification_instance["durable"])
        self.assertFalse(classification_instance["durableCommitted"])
        self.assertEqual(
            classification_instance["source"],
            "test-diagnostic-placeholder",
        )
        self.assertEqual(diagnostic_instance, classification_instance)
        self.assertEqual(direct_placeholder, classification_instance)
        self.assertEqual(known["status"], "known")
        self.assertEqual(known["instanceId"], "encounter-run-7")
        self.assertEqual(known["proposedInstanceId"], "encounter-run-7")
        self.assertFalse(known["placeholder"])
        self.assertTrue(known["durableCommitted"])


    def test_actor_diagnostic_rows_are_generic_and_entries_key_aware(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const group = encounter.classifyActorGroup({
              actorIds: ["enemy.one", "enemy.two", "enemy.three"],
              snapshot: {
                characters: {
                  "enemy.one": {status: "active", health: 12},
                  "enemy.two": {status: "down", health: 0}
                }
              },
              actorCollectionKey: "characters",
              entriesKey: "boarders"
            });
            const rows = encounter.actorDiagnosticRows(group, {
              entriesKey: "boarders"
            });

            console.log(JSON.stringify({
              rowCount: rows.length,
              first: rows[0],
              second: rows[1],
              third: rows[2]
            }));
            """
        )

        self.assertEqual(result["rowCount"], 3)
        self.assertEqual(result["first"]["id"], "enemy.one")
        self.assertEqual(result["first"]["status"], "active")
        self.assertEqual(result["first"]["health"], 12)
        self.assertTrue(result["first"]["active"])
        self.assertFalse(result["first"]["defeated"])
        self.assertEqual(result["second"]["status"], "down")
        self.assertTrue(result["second"]["defeated"])
        self.assertTrue(result["third"]["missing"])


    def test_completion_diagnostic_names_untrusted_stale_completed_state(self) -> None:
        result = self.run_node(
            r"""
            const encounter = require(process.argv[1]);

            const classification = encounter.classifyStagedEncounterState({
              key: "scenario.alpha:encounter.boarding-defense",
              definitionId: "encounter.boarding-defense",
              scenarioId: "scenario.alpha",
              view: {visible: true, state: {status: "active", stageId: "investigation"}},
              actorGroup: {
                status: encounter.ACTOR_GROUP_STATUS.defeated,
                total: 2,
                activeCount: 0,
                defeatedCount: 2,
                missingCount: 0,
                actors: []
              },
              activeStageId: "combat",
              completedStageId: "investigation"
            });
            const plan = encounter.reconciliationPlan(classification, {
              recoverDefeated: true
            });
            const diagnostic = encounter.diagnosticSnapshot(classification, plan);

            console.log(JSON.stringify({
              status: diagnostic.status,
              recovery: diagnostic.recovery,
              completion: diagnostic.completion,
              direct: encounter.completionDiagnostic(classification, plan)
            }));
            """
        )

        self.assertEqual(result["status"], "recoverable-completed-defeated")
        self.assertEqual(result["recovery"], "restart-encounter")
        completion = result["completion"]
        self.assertEqual(completion, result["direct"])
        self.assertEqual(completion["status"], "completed-but-untrusted")
        self.assertTrue(completion["completed"])
        self.assertFalse(completion["trusted"])
        self.assertEqual(completion["reason"], "durable-instance-missing")
        self.assertEqual(completion["staleActorState"], "stale-defeated-actors")
        self.assertTrue(completion["restartable"])
        self.assertEqual(completion["corruption"], "restartable-corruption")
        self.assertEqual(
            completion["issueCodes"],
            [
                "durable-instance-missing",
                "stale-defeated-actors",
                "restartable-corruption",
            ],
        )



if __name__ == "__main__":
    unittest.main()
