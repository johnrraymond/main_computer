# MCEL Promise Gap: Public Contract Probe Integrity

The public DSL test probes are *diagnostic evidence*, not acceptance of an authored application or certification of MCEL's promise.

## Keyed-list probe correction

The keyed-list positive probe must declare a record containing a string identity field (`sku`) and return its state handle in `states`. A list of plain strings cannot be keyed by a nonexistent `sku` field. The complementary negative probe intentionally attempts that invalid declaration and must be refused with `MCEL_DSL_LIST_KEY_INVALID`. Compiler identity validation must not be weakened to make a defective positive probe pass.

## Exported semantics requirement

Constructing a DSL handle does not necessarily export it into Application IR. A successful compiler process with no relevant states, surfaces, or intents provides no evidence that the public authoring interface supplies the capability.

The Promise Gap Frob therefore checks normalized IR for each positive composition probe:

- **Keyed collection:** exported canonical list state with record items and a declared string identity field.
- **Record schema:** exported composite record state.
- **Validated form:** exported form node with field constraints and a declared intent reference.
- **Keyed surface:** exported collection node bound to a keyed state and governed add/update/remove intents.

The Frob reports `BLOCKED` when the emitted IR does not demonstrate the expected structure, even if compilation succeeds. It reports `AVAILABLE_UNPROVEN` when the relevant structure is present; that status **does not** prove application behavior, persistence, effects, design quality, or authoring economy.

The canonical task packet, release authority, Website Builder, and production application bindings remain unchanged. Existing browser acceptance tests remain separate from the promise verdict.
