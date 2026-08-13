export default defineGameplayPack({
  id: "pack.experimental.heavy-shuttle-juggernaut",
  title: "Heavy Shuttle Juggernaut",
  version: "0.1.0",

  setup(pack) {
    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        encounter.spawnWave({
          id: "juggernaut-spawn",
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              displayName: "Heavy Juggernaut",
              healthMultiplier: 5.0,
              visualModifier: "heavy-armor"
            }
          ],
          hudMessage: "Warning: Heavy presence detected in shuttle corridor!"
        });

        encounter.setHostileHealthMultiplier(1.5);
      });
    });
  }
});
