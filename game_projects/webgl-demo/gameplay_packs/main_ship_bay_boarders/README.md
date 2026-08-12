# Main Ship: Bay Boarders

This is the real-JavaScript gameplay pack for the first main-ship section pack.

It is supported by the section command harness, but it is intentionally not wired into live gameplay yet. The purpose is to prove the next authoring surface after the opening-shuttle combat pack.

The pack proves the next packability target:

- hook the main-ship bay entry section;
- trigger when the `main-ship-bay-entry` cutscene resolves;
- treat normal cutscene completion and user skip as the same resolved event;
- show a HUD warning;
- set a clear-bay objective;
- spawn boarders in the bay;
- complete the section when boarders are cleared;
- fail the section if the player is defeated.

The desired authoring feel is still real JavaScript:

```js
pack.section("main-ship-bay", (section) => {
  section.onCutsceneResolved("main-ship-bay-entry", () => {
    section.spawnWave(...);
  });
});
```

The runtime now mirrors the shuttle YAGNI path up through command emission: load `pack.js`, run `setup(pack)` in a section command harness, and emit validated commands. A later main-ship scene adapter must apply only whitelisted commands.

This pack must not receive raw renderer, DOM, filesystem, network, save-state, or project mutation access.
