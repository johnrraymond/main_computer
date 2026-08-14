export default defineGameplayPack({
  id: "pack.experimental.large-random-shuttle-boarder",
  title: "Large Shuttle Boarder",
  version: "0.1.0",

  setup(pack) {
    const SHUTTLE_LOCATIONS = ["shuttle-vent-left", "shuttle-vent-right", "shuttle-corridor-mid"];

    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        const targetLocation = SHUTTLE_LOCATIONS[Math.floor(Math.random() * SHUTTLE_LOCATIONS.length)];

        encounter.spawnWave({
          id: "large-boarder-spawn",
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              healthMultiplier: 2.0,
              scale: 2.0,
              displayName: "Large Boarder"
            }
          ],
          location: targetLocation,
          hudMessage: "A massive presence detected in the shuttle vents!"
        });
      });
    });
  }
});
