BEGIN manifest.json
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.large-random-shuttle-boarder",
  "title": "Large Elite Boarder Ambush",
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
const PACK_ID = "pack.experimental.large-random-shuttle-boarder";
const SHUTTLE_LOCATIONS = ["location.shuttle.port", "location.shuttle.starboard", "location.shuttle.aft"];
const HEALTH_MULTIPLIER = 2;

export default defineGameplayPack({
  id: PACK_ID,
  title: "Large Elite Boarder Ambush",
  version: "0.1.0",

  setup(pack) {
    pack.encounter("opening-shuttle-ambush", (encounter) => {
      encounter.onStart(() => {
        // Increase health multiplier for the incoming elite unit
        encounter.setHostileHealthMultiplier(HEALTH_MULTIPLIER);

        // Select a random valid shuttle location
        const targetLocation = SHUTTLE_LOCATIONS[Math.floor(Math.random() * SHUTTLE_LOCATIONS.length)];

        // Spawn one large, tough boarder in the selected location
        encounter.spawnWave({
          id: "large-elite-boarder-wave",
          actors: [
            {
              archetype: "shuttle-raider",
              count: 1,
              displayName: "Heavy Boarder",
              healthMultiplier: HEALTH_MULTIPLIER,
              scale: 2.0
            }
          ],
          location: targetLocation,
          hudMessage: "Warning: Large hostile signature detected near shuttle!"
        });

        encounter.showHudMessage("Alert: An Elite Boarder has breached the perimeter!");
      });
    });
  }
});
END pack.js