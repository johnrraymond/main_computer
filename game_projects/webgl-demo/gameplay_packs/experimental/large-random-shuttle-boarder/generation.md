BEGIN manifest.json
{
  "schema": "game.gameplayPackJsManifest.v1",
  "kind": "gameplay-pack-js",
  "manifestVersion": "gameplay-pack-js.manifest.v1",
  "id": "pack.experimental.heavy-shuttle-juggernaut",
  "title": "Heavy Shuttle Juggernaut",
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
END pack.js