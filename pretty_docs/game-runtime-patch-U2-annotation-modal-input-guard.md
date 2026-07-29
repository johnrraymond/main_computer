# Game Runtime Patch U.2 — Annotation Modal Input Guard

## Purpose

Patch U added hold-P annotation and Patch U.1 expanded picking to one-off
rendered primitives. Patch U.2 fixes the editor modal input behavior: once the
annotation dialog is open, gameplay keyboard controls must not keep moving or
firing the player, and text fields must receive normal typing.

## Runtime behavior

When the annotation dialog opens, the shuttle-3D surface now:

```text
clears active movement keys
clears the held polygon-annotation modifier
stops click-drag lookaround state
lets input and textarea key events pass through the dialog
blocks gameplay key handling while the dialog is open
cleans up the modal on close, including Escape/browser dialog close
```

This means letters such as `W`, `A`, `S`, and `D` can be typed into the
annotation label, notes, and tags fields instead of being captured as movement
controls.

## Why this was needed

The annotation modal is editor tooling layered over an active game surface. The
same keys used for game control are also common typing characters. Without an
explicit modal guard, bubbling key events from the dialog could be intercepted by
the shuttle-3D lookaround binding.

## Acceptance checks

```text
opening the annotation dialog clears existing movement input
W/A/S/D typed in the notes field are not intercepted by gameplay movement
gameplay keydown/keyup handling is suspended while the modal is open
click-drag lookaround and phaser fire do not resume until the modal closes
normal gameplay controls are unchanged after the dialog closes
```

## Files

```text
main_computer/web/applications/scripts/scene-viewer.js
tests/test_shuttle3d_polygon_annotation_tool.py
```
