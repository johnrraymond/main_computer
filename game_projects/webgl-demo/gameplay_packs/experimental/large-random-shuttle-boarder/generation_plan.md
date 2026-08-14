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
  "title": "Large Shuttle Boarder",
  "summary": "Spawns a single large, high-health hostile at the start of the encounter.",
  "steps": [
    {
      "id": "step-1",
      "event": "onStart",
      "summary": "Spawn a single powerful hostile at a shuttle location.",
      "commands": [
        "spawnWave"
      ]
    }
  ]
}
END pack_plan.json