BEGIN manifest.json
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.large-random-shuttle-boarder",
  "title": "Large Shuttle Boarder Ambush",
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
  id: "pack.experimental.large-random-shuttle-boarder",
  title: "Large Shuttle Boarder Ambush",
  version: "0.1.0",

  setup(pack) {
    const SHUTTLE_LOCATIONS = ["loc.shuttle.port", "loc.shuttle.starboard", "loc.shuttle.aft"];
    const HEALTH_MODIFIER = 2.5;

    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        const randomLocation = SHUTTLE_LOCATIONS[Math.floor(Math.random() * SHUTTLE_LOCATIONS.length)];

        encounter.spawnWave({
          id: "large-shuttle-boarder-wave",
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              displayName: "Large Shuttle Boarder",
              healthMultiplier: HEALTH_MODIFIER,
              location: randomLocation
            }
          ],
          hudMessage: "Warning: A massive presence detected near the shuttle hull!"
        });

        encounter.showHudMessage("An unexpected large-scale boarding attempt has begun!");
      });
    });
  }
});
END pack.js