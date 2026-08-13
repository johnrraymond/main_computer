TASK: Generate one experimental JavaScript gameplay pack for Main Computer.

Return EXACTLY two files using the file markers below.
Do not explain anything.
Do not summarize the examples.
Do not ask questions.
Do not complete or rewrite the runtime.
Do not include markdown fences unless they are inside the markers.

REQUIRED OUTPUT CONTRACT:
BEGIN manifest.json
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.short-descriptive-id",
  "title": "Short Descriptive Title",
  "version": "0.1.0",
  "entry": "pack.js",
  "targets": {
    "encounter": "opening-shuttle-ambush"
  },
  "experimental": true,
  "generated": true,
  "defaultEnabled": false,
  "hiddenFromLobby": true
}
END manifest.json

BEGIN pack.js
export default defineGameplayPack({
  id: "pack.experimental.short-descriptive-id",
  title: "Short Descriptive Title",
  version: "0.1.0",

  setup(pack) {
    // Use exactly this target shape:
    // pack.encounter("opening-shuttle-ambush", ...)
  }
});
END pack.js

USER INTENT:
At the start, spawn one large hostile boarder in a random valid shuttle location. Make it tougher and visually distinct. Additive only.

HARD TARGET:
- Project: webgl-demo
- Existing scenario id: opening-shuttle-ambush
- Existing scenario kind: encounter
- Required API shape: pack.encounter("opening-shuttle-ambush", ...)
- Use exactly the scenario id "opening-shuttle-ambush".
- Do not create a new scenario id.
- Do not modify base scenario files.
- The generated pack must be additive to the existing scenario.

CURRENTLY ALLOWED EVENTS:
[
  "onStart",
  "onHostilesDefeated",
  "onAllHostilesDefeated",
  "onDestinationReached",
  "onPlayerDefeated"
]

CURRENTLY ALLOWED COMMANDS:
[
  "setHostileHealthMultiplier",
  "showHudMessage",
  "spawnWave",
  "setObjective",
  "complete",
  "fail"
]

SAFETY RULES:
- pack.js must use export default defineGameplayPack(...).
- Do not use imports.
- Do not use these globals: window, document, fetch, XMLHttpRequest, localStorage, sessionStorage, eval, Function.
- Do not use raw renderer, DOM, network, filesystem, browser storage, or save-state access.
- Manifest must keep experimental=true, generated=true, defaultEnabled=false, hiddenFromLobby=true.
- Generated behavior must be additive only.

REFERENCE LOCAL GAME PACKS FOR STYLE ONLY:
Do not summarize these files. Do not complete them. Use them only as examples of the authoring style.

### Reference file: game_projects/webgl-demo/gameplay_packs/opening_shuttle_elite_boarders/manifest.json

```text
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.opening-shuttle.elite-boarders",
  "title": "Opening Shuttle: Elite Boarders",
  "version": "0.1.0",
  "description": "Live YAGNI JavaScript gameplay pack for the opening shuttle encounter. It triples shuttle raider health, spawns an Elite Boarding Leader after two hostiles are defeated, and completes when Haven orbit is reached.",
  "entry": "pack.js",
  "authoringModel": {
    "language": "javascript",
    "sdk": "defineGameplayPack",
    "runtimeStatus": "yagni-runtime-active",
    "purpose": "Working real-JS gameplay pack for the opening-shuttle YAGNI command harness",
    "executionModel": "setup registers handlers; handlers emit validated shuttle commands"
  },
  "targets": {
    "projectId": "webgl-demo",
    "scene": "opening-shuttle",
    "encounter": "opening-shuttle-ambush",
    "template": "shuttle-ambush",
    "systemId": "system.solace-reach",
    "destinationId": "destination.solace-reach.haven-orbit"
  },
  "permissions": {
    "spawnActors": true,
    "modifyCombatStats": true,
    "showHudMessages": true,
    "setObjectives": true,
    "grantReceipts": true,
    "completeEncounter": true,
    "writeSaveState": false,
    "accessNetwork": false,
    "accessFilesystem": false,
    "accessDom": false,
    "rawRendererAccess": false,
    "rawRuntimeStateMutation": false
  },
  "contracts": {
    "registersEncounterHandlers": [
      "onStart",
      "onHostilesDefeated",
      "onAllHostilesDefeated",
      "onDestinationReached",
      "onPlayerDefeated"
    ],
    "emitsCommands": [
      "set-hostile-health-multiplier",
      "show-hud-message",
      "set-objective",
      "spawn-wave",
      "complete-encounter",
      "fail-encounter"
    ],
    "forbiddenGlobals": [
      "window",
      "document",
      "fetch",
      "XMLHttpRequest",
      "localStorage",
      "sessionStorage",
      "eval",
      "Function"
    ]
  },
  "safety": {
    "dataOnlyManifest": true,
    "packJsMustRunInSandbox": true,
    "commandsMustBeValidatedByTemplateRunner": true,
    "noArbitraryEngineMutation": true
  },
  "defaultEnabled": true
}
```
### Reference file: game_projects/webgl-demo/gameplay_packs/opening_shuttle_elite_boarders/pack.js

```text
const PACK_ID = "pack.opening-shuttle.elite-boarders";
const HEALTH_MULTIPLIER = 3;
const BASE_RAIDER_DEFEATS_BEFORE_LEADER = 2;
const HAVEN_ORBIT_DESTINATION = "destination.solace-reach.haven-orbit";

export default defineGameplayPack({
  id: PACK_ID,
  title: "Opening Shuttle: Elite Boarders",
  version: "0.1.0",

  targets: {
    projectId: "webgl-demo",
    scene: "opening-shuttle",
    encounter: "opening-shuttle-ambush",
    template: "shuttle-ambush",
    systemId: "system.solace-reach",
    destinationId: HAVEN_ORBIT_DESTINATION
  },

  permissions: {
    spawnActors: true,
    modifyCombatStats: true,
    showHudMessages: true,
    setObjectives: true,
    grantReceipts: true,
    completeEncounter: true,
    writeSaveState: false,
    accessNetwork: false,
    accessFilesystem: false,
    accessDom: false,
    rawRendererAccess: false,
    rawRuntimeStateMutation: false
  },

  setup(pack) {
    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        encounter.setHostileHealthMultiplier(HEALTH_MULTIPLIER);

        encounter.setObjective({
          id: "survive-triple-strength-boarders",
          label: "Survive the triple-strength boarding party",
          required: true
        });

        encounter.showHudMessage(
          "Elite Boarders pack active: shuttle raiders have 3x health."
        );
      });

      encounter.onHostilesDefeated(BASE_RAIDER_DEFEATS_BEFORE_LEADER, () => {
        encounter.spawnWave({
          id: "elite-boarding-leader",
          trigger: {
            type: "after-hostile-defeats",
            count: BASE_RAIDER_DEFEATS_BEFORE_LEADER
          },
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              displayName: "Elite Boarding Leader",
              healthMultiplier: HEALTH_MULTIPLIER
            }
          ],
          hudMessage: "Elite Boarding Leader inbound."
        });

        encounter.setObjective({
          id: "defeat-elite-boarding-leader",
          label: "Defeat the Elite Boarding Leader",
          required: true
        });
      });

      encounter.onAllHostilesDefeated(() => {
        encounter.setObjective({
          id: "reach-haven-orbit",
          label: "Reach Haven orbit",
          required: true
        });
      });

      encounter.onDestinationReached(HAVEN_ORBIT_DESTINATION, () => {
        encounter.complete({
          receipt: "receipt.opening-shuttle.elite-boarders-cleared",
          message: "Elite boarders defeated. Shuttle route secured."
        });
      });

      encounter.onPlayerDefeated(() => {
        encounter.fail({
          reason: "shuttle-overrun",
          message: "The triple-strength boarding party overwhelms the shuttle."
        });
      });
    });
  }
});
```

FINAL REMINDER:
Return only this exact two-file marker format, filled in with the generated pack:

BEGIN manifest.json
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.short-descriptive-id",
  "title": "Short Descriptive Title",
  "version": "0.1.0",
  "entry": "pack.js",
  "targets": {
    "encounter": "opening-shuttle-ambush"
  },
  "experimental": true,
  "generated": true,
  "defaultEnabled": false,
  "hiddenFromLobby": true
}
END manifest.json

BEGIN pack.js
export default defineGameplayPack({
  id: "pack.experimental.short-descriptive-id",
  title: "Short Descriptive Title",
  version: "0.1.0",

  setup(pack) {
    // Use exactly this target shape:
    // pack.encounter("opening-shuttle-ambush", ...)
  }
});
END pack.js
