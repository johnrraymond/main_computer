TASK: Break a requested gameplay pack into a small implementation plan.

Return EXACTLY one JSON file using the markers below.
Do not write pack.js.
Do not write manifest.json.
Do not explain anything.
Do not ask questions.

REQUIRED OUTPUT CONTRACT:
BEGIN pack_plan.json
{
  "schema": "game.gameplayPackGenerationPlan.v1",
  "kind": "gameplay-pack-generation-plan",
  "project": "webgl-demo",
  "scenario": {
    "id": "opening-shuttle-ambush",
    "kind": "encounter",
    "apiCall": "pack.encounter("opening-shuttle-ambush", ...)"
  },
  "packId": "pack.experimental.large-random-shuttle-boarder",
  "title": "Short Descriptive Title",
  "summary": "One sentence summary of the generated pack.",
  "steps": [
    {
      "id": "step-1",
      "event": "onStart",
      "summary": "One small behavior step.",
      "commands": ["showHudMessage"]
    }
  ]
}
END pack_plan.json

USER INTENT:
At the start, spawn one large hostile boarder in a random valid shuttle location. Make it tougher and visually distinct - twice as big around. Additive only.

HARD TARGET:
- Project: webgl-demo
- Existing scenario id: opening-shuttle-ambush
- Existing scenario kind: encounter
- Required API shape: pack.encounter("opening-shuttle-ambush", ...)
- Use exactly the scenario id "opening-shuttle-ambush".
- Do not create a new scenario id.
- The generated behavior must be additive to the existing scenario.

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

PLAN RULES:
- Use one to three steps.
- Each step must name one allowed event.
- Each step must list only allowed commands.
- Keep each step small enough to implement directly in one pack.js handler.
- The plan should capture the user's requested behavior, but do not invent engine APIs.
- If an idea cannot be expressed with allowed events/commands, omit it from the plan summary rather than inventing new APIs.

FINAL REMINDER:
Return only this exact one-file marker format, filled in with the plan:

BEGIN pack_plan.json
{
  "schema": "game.gameplayPackGenerationPlan.v1",
  "kind": "gameplay-pack-generation-plan",
  "project": "webgl-demo",
  "scenario": {
    "id": "opening-shuttle-ambush",
    "kind": "encounter",
    "apiCall": "pack.encounter("opening-shuttle-ambush", ...)"
  },
  "packId": "pack.experimental.large-random-shuttle-boarder",
  "title": "Short Descriptive Title",
  "summary": "One sentence summary of the generated pack.",
  "steps": [
    {
      "id": "step-1",
      "event": "onStart",
      "summary": "One small behavior step.",
      "commands": ["showHudMessage"]
    }
  ]
}
END pack_plan.json
