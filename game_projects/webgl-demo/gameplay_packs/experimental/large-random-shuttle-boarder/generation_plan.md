BEGIN pack_plan.json
{
  "schema": "game.gameplayPackGenerationPlan.v1",
  "kind": "gameplay-pack-generation-plan",
  "project": "webgl-demo",
  "scenario": {
    "id": "opening-shuttle-ambush",
    "kind": "encounter",
    "apiCall": "pack.encounter(\"opening-shuttle-ambush\", ...)"
  },
  "packId": "pack.experimental.large-random-shuttle-boarder",
  "title": "Large Elite Boarder Ambush",
  "summary": "Spawns an enhanced, high-health hostile unit at the start of the encounter.",
  "steps": [
    {
      "id": "step-1",
      "event": "onStart",
      "summary": "Increase health multiplier and spawn the elite boarder wave.",
      "commands": [
        "setHostileHealthMultiplier",
        "spawnWave",
        "showHudMessage"
      ]
    }
  ]
}
END pack_plan.json