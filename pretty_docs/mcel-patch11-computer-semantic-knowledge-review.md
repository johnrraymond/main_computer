# Patch 11.0 — Computer semantic knowledge: implementation and acceptance review

**Verdict:** bounded `PASS` for initial read-only semantic knowledge and interpreter; **NOT** a complete MCEL semantic language, full application integration, or promise closure.

## Delivered

- 26 computer-native concept entries in a stand-alone JSON vocabulary (identity, quantities, timestamps, structured paths/commands/endpoints, processes, connections, CPU/GPU, services, schedules, operations).
- Eight producer-bound Task Manager observation families with scoped identity, typed fields, availability, and provenance.
- Six declared-only Task Manager operations; no executable capability is invented.
- Generic fragment loader, validation, interpretation, explanation and CLI with no UI import or OS action path.
- A read-only Frob with falsifiable individual assertions and saved JSON reporting.
- An independent **catalog extension test** adds a second synthetic schema to the temporary catalog *without creating a fake application*. This checks loader generality but is not yet cross-application certification.

## Acceptance matrix

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| S11-U01: meaning before layout | PASS, bounded | No display/layout directives, no rendering module in first cut |
| S11-U02: native computing knowledge | PASS, first inventory | 26 concepts / 8 Task Manager sources |
| S11-U03: actual producer binding | PASS, offline schema | Producer-shaped snapshot and source field trace; **no live endpoint read** |
| S11-U04: preserve original information and availability | PASS, unit/Frob | Unknown RSS vs zero, typed validity, exact original values |
| S11-U05: identities and relationships do not overclaim | PASS, unit/Frob | PID+creation; incomplete/duplicate identity; candidate-only PID link |
| S11-U06: declared operations do not execute | PASS | All descriptions refuse authority/execution |
| S11-U07: extension from other apps | PARTIAL | Separate fragment test PASS, no other actual app audited yet |
| S11-U08: alignment with existing MCEL Application IR | UNVERIFIED | Explicit non-invasive boundary; compiler lowering remains future |
| S11-U09: unhinted high-quality layout | NOT ATTEMPTED | Deliberately deferred until database has real multi-app breadth |
| S11-U10: authoring economy | NOT ATTEMPTED | Requires multi-application study |
| S11-T01: repeatable diagnostics | PASS | `python -m tools.mcel_semantic_knowledge_frob`, JSON report |
| S11-T02: Task Manager / Patch 10 regression | PASS for producer regression; other tests depend on omitted export fixture | See local results below |

## Observed verification in the preparation environment

- `python -m pytest tests/test_mcel_computer_knowledge.py -q`: 15 passed.
- `python -m tools.mcel_semantic_knowledge_frob`: all 11 assertions PASS.
- `python -m pytest tests/test_mcel_task_manager_patch10_producer.py tests/test_mcel_computer_knowledge.py -q`: 17 passed.
- `tests/test_mcel_task_manager_notebook_dsl.py` cannot pass from the provided source export because `mcel_experiments/task-manager-notebook/application.js` is omitted.
- `tests/test_task_manager.py` cannot collect from this export because its web-loader import requires an omitted `game_projects/webgl-demo/game.json` fixture.
- No Windows server or live Task Manager acceptance was executed in this environment. Existing user-hosted Patch 10 PASS evidence is preserved; it is not re-certified by this patch.

## Stop conditions before the next level

Do **not** move on to UI inference, hints, a universal compiler integration, or effects until existing File Explorer and another real application have contributed grounded semantic fragments and independent evidence. Do not install an unnecessary graph database; file-based ontology is currently the simplest viable evidence source. If extensions require repeated changes to the interpreter for application names, this architecture has failed its generality goal.
