# MCEL intrinsic semantic working units

This is an opt-in presentation capability for paired MCEL semantic HTML. It does **not** change authority, inputs, actions, application navigation, activity ownership, or the default dimensions of unrelated regions.

## Authored contract

Sibling regions that belong to one control-and-result unit may declare the same `data-mcel-prefer-width-group` token (a stable lowercase semantic identifier). Each group member declares `data-mcel-prefer-sizing="intrinsic"`. A result may additionally declare `data-mcel-prefer-align="end"` when its value should be aligned against the working unit's trailing edge. Existing `data-mcel-prefer-density="compact"` remains an independent control for control heights and spacing.

For example, the Calculator declares the `calculation` group on its primary result and primary compute input. The semantic HTML parser compiles these to `preferences.widthGroup`, `preferences.sizing`, and `preferences.align`. The paired hierarchy validator rejects unpaired groups, cross-parent grouping, conflicting sizing modes, invalid identifiers, and alignment or intrinsic sizing without a width group.

Only the natural-workspace renderer composes matching sibling region elements into a shared bounded working unit. It preserves the original region nodes, value bindings and event handlers. A hidden group is excluded from layout. The shared CSS uses intrinsic sizing, constrained by the available workspace, and applies trailing-edge alignment to the result's *actual state-value wrapper*, not merely its nested output text.

The semantic working unit's width follows content and usable control targets; the expression input fills only the unit while Evaluate retains its intrinsic label width. No fixed application workspace width, absolute positioning, or application-ID-specific styling is introduced. Applications without the new preferences continue to follow their existing composition and CSS contracts. Planned workspaces are not remapped.

## Acceptance

`tests/test_mcel_semantic_intrinsic_width_groups.py` verifies semantic validation and the live horizontal relationships among result, expression, Evaluate and keypad at desktop, medium and phone widths. It exercises a real evaluation and switches to Graphing and back, ensuring the group disappears and restores correctly. `tests/test_mcel_calculator_compact_density.py` continues to verify keypad heights, vertical fit, scrolling, and both submission paths. Existing File Manager, Task Manager, Code Editor and layout-composition oracles remain applicable without layout changes.
