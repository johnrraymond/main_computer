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
