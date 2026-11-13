# MCEL semantic theme Lab preview v1 (Baby Step 2)

## Authority and scope

The source of truth remains `mcel_apps/calculator/application.js`. The server
compiles this DSL as it does for Calculator's host projection. The Lab include
`mcel-lab-canonical-presentation-source.js` is **virtual**, emitted by
`main_computer/mcel_lab_semantic_presentation_source.py`, and contains the
canonical IR's intents, nodes, semanticSurface controls and regions, with no
host markup and no alternate hand-authored action taxonomy. Compilation failure
aborts the page build instead of manufacturing semantic identities.

`McelSemanticPresentationBridge.observePresentationTargets()` resolves
semanticSurface's source selectors against the actual mounted root and joins
control intent source names to exactly one canonical intent and its node.
Ambiguous joins, missing roots, invalid selectors, and unmatched components are
reported; not inferred from labels or styling.

`McelSemanticThemePreview` uses those source-proven targets only. It obtains
color tokens from the **existing** Style Law theme catalog and the
`.mcel-runtime-preview.theme-*` styles in `mcel-lab.css`. The projection
uses only background-color, text color, and border-color. It changes no
layout metrics, enabled states, event handlers, operational authority,
semantic IR, navigation, or Chrome Law. It does **not** assign invented
Start/Stop/Enable/Delete meaning to actions lacking such an IR classification.

## How to exercise

Open MCEL Lab > Theme & Chrome. Select a theme, then click **Enable Calculator
Preview**. The Lab mounts `/applications/calculator?mcel_lab_theme_preview=1`
in a separate, same-origin test frame. The active theme is applied after the
frame loads; changing the theme updates only this frame. Changing Chrome
continues to affect the legacy rendered-site example but never the Calculator
preview. **Disable Preview** unloads that frame and its temporary styling.
Production Calculator pages have no opt-in preview markers or styling. Frame
isolation is a presentation boundary, not a browser security sandbox against
same-origin code; the preview runs the ordinary Calculator app runtime.

## Verification

The oracle compiles the real Calculator DSL and host markup, checks identities
against canonical source records, switches three contrasting existing themes,
and compares control bounding boxes and disabled states at 390/950/1440 px.
A control that merely displays the word 'Start' is never assigned a semantic
identity, and ambiguous source-intent bindings are rejected.

## Explicit exclusions

No production default theme, app-specific CSS migration, Chrome composition,
semantic action-color taxonomy, external application theming, or runtime
mutation of the Calculator's operation semantics. This is an opt-in first
application proof, not a general theme deployment.
