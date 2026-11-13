# MCEL Semantic Presentation Observation — Baby Step 1

Status: **read-only compatibility diagnostic**, not an appearance or chrome migration.

`McelSemanticPresentationBridge.observeCompatibility({ir, root, themeLaw, chromeLaw, theme, chrome})` accepts an existing `mcel.application-ir.v1` document or `mcel.semantic-surface-ir.v1`, an optional live DOM root, and the published Style and Chrome Law catalogs. It produces `mcel.semantic-presentation-observation.v1`. No new semantic authority is created.

The bridge reads canonical IDs from the IR and exact `data-mcel-node`, `data-mcel-node-id`, `data-mcel-control`, `data-mcel-region`, or `data-intent` bindings in the rendered DOM. `data-mc-component-id` is only counted as a binding if it exactly matches a canonical record. A label, route, CSS selector, button color, and DOM position are never treated as semantic identity. Runtime disabled state is recorded only as a DOM observation and is **not** a proof of backend authorization. Duplicate canonical IDs produce an ambiguous result, not a match.

The report separates the canonical schema and application identity, declared records, rendered exact/ambiguous/unbound elements, source-backed operation kind and risk, legacy style attributes, available theme and chrome catalog entries, and explicitly unresolved modern presentation mappings. In particular, `operationKind: capability` and `risk: consequential` do not determine whether an action means Start, Stop, Enable, Disable, Inspect, or Delete. `actionMeaning` remains `null` until such a semantic classification is declared by an authoritative source.

MCEL Lab's Theme & Chrome workspace hosts a **Semantic Compatibility** inspector. Available source modes are its generated-site preview DOM, the selected live blueprint DOM, and the loaded Task Manager candidate IR (explicitly **not** production evidence, with a mounted DOM only if the active candidate status proves the identical source fingerprint). These modes make missing IR and missing runtime bindings visible. Re-inspecting after a theme/chrome selection may change catalog metadata, but never canonical observations. Nothing in this patch makes Theme or Chrome control a modern application's appearance or layout.

The existing laws retain their operational behavior. The observer never calls Style Law `applyRuntimeLaw`, Chrome Law `applyChromeHtml`, any mutation or intent method, or MCEL layout inference. The inspector writes only to its own report area, using text rather than injecting source-controlled HTML.

Acceptance: `python -m pytest -q tests/test_mcel_semantic_presentation_bridge.py`. The oracle checks exact mapping, source provenance, missing and ambiguous mappings, label non-inference, theme/chrome independence, canonical surface IR reuse, and nonmutation. This is the compatibility measurement boundary for a later, separately authorized presentation integration.
