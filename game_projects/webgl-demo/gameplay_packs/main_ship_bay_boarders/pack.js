const PACK_ID = "pack.main-ship.bay-boarders";
const SECTION_ID = "main-ship-bay";
const CUTSCENE_ID = "main-ship-bay-entry";
const BAY_LOCATION = "bay.shuttle";
const BOARDER_ARCHETYPE = "ship-boarder";
const BOARDER_COUNT = 3;
const BOARDER_HEALTH_MULTIPLIER = 1.5;
const CLEAR_BAY_RECEIPT = "receipt.main-ship.bay-boarders-cleared";

export default defineGameplayPack({
  id: PACK_ID,
  title: "Main Ship: Bay Boarders",
  version: "0.1.0",

  targets: {
    projectId: "webgl-demo",
    scene: "mother-ship",
    section: SECTION_ID,
    template: "ship-interior-boarder-response",
    cutsceneId: CUTSCENE_ID,
    locations: [BAY_LOCATION]
  },

  permissions: {
    spawnActors: true,
    modifyCombatStats: true,
    showHudMessages: true,
    setObjectives: true,
    grantReceipts: true,
    completeSection: true,
    writeSaveState: false,
    accessNetwork: false,
    accessFilesystem: false,
    accessDom: false,
    rawRendererAccess: false,
    rawRuntimeStateMutation: false
  },

  setup(pack) {
    pack.section(SECTION_ID, (section) => {
      section.onCutsceneResolved(CUTSCENE_ID, () => {
        section.showHudMessage(
          "Bay Boarders pack active: boarders detected in the main bay."
        );

        section.setObjective({
          id: "clear-main-bay-boarders",
          label: "Clear the boarders from the main bay",
          required: true
        });

        section.spawnWave({
          id: "main-ship-bay-boarders",
          trigger: {
            type: "cutscene-resolved",
            cutsceneId: CUTSCENE_ID
          },
          location: BAY_LOCATION,
          actors: [
            {
              archetype: BOARDER_ARCHETYPE,
              count: BOARDER_COUNT,
              displayName: "Main Ship Boarder",
              healthMultiplier: BOARDER_HEALTH_MULTIPLIER
            }
          ],
          hudMessage: "Hostile boarders are breaching the bay. Clear the deck."
        });
      });

      section.onAllHostilesDefeated(() => {
        section.setObjective({
          id: "secure-main-bay",
          label: "Main bay secured",
          status: "complete"
        });

        section.complete({
          receipt: CLEAR_BAY_RECEIPT,
          message: "Main bay boarders cleared."
        });
      });

      section.onPlayerDefeated(() => {
        section.fail({
          reason: "main-bay-overrun",
          message: "Main bay boarders overran the landing zone."
        });
      });
    });
  }
});
