# Patch 14.7.3 — Source Disappearance and Semantic Element Retirement

## Scope and boundaries

This patch adds an **opt-in, read-only laboratory** mechanism to the existing universal semantic collection. It does **not** infer that a process has terminated simply because an observation no longer contains it. The UI labels retained data **“No longer in current observation”**, not “terminated.” A source membership change is not a command to erase the user's presentation.

The shared `McelElementRetirement` service has **no Task Manager-specific fields**, network access or write operations. A list row's retirement behavior is determined by a validated element policy; the container supplies visibility information. The original source records remain separate from retained presentation snapshots: `collection.ids` and `collection.source` report only current observations. `collection.retirement` reports retained presentations.

## Enable in the existing lab

The live application's ordinary routes are unchanged. Opt in when mounting an existing universal semantic lab:

```js
const lab = McelUniversalSemanticLayoutLab.mount(host, plan, {
  lab: true,
  presentationNegotiation: true,
  sourceRetirement: true,
  retirementPolicy: {
    schema: 'mcel.element-retirement-policy.v1',
    visible: 'timed',
    offscreen: 'remove',
    delayMs: 5000,
    pauseWhen: ['text-selected', 'focused', 'hovered', 'expanded'],
    onPause: 'reset',
    maxRetained: 40
  }
});
```

To request different behavior for an individual element, supply a trusted `retirementPolicies` object keyed by its current canonical row ID (each value must satisfy the same schema). An element-specific policy takes precedence over the registered row policy, the collection policy and the service default, in that order. These are **trusted presentation policies**, never commands read from raw process data.

## Policy semantics

- `visible`: `remove` (unless protected), `timed`, or `retain` (requires explicit release).
- `offscreen`: the same options, evaluated from the scroll container's visibility report at source disappearance. An offscreen element with a selected text range remains protected even if its ordinary offscreen rule is `remove`.
- `delayMs`: 0–120000 milliseconds for timed retirement.
- `pauseWhen`: any subset of `text-selected`, `focused`, `hovered`, and `expanded`.
- `onPause`: `resume` saves remaining time when protection begins; `reset` restarts the full duration after protection ends; `continue` keeps the deadline running while protecting the presentation against destruction.
- `maxRetained`: a bounded soft capacity; excess unprotected presentations are refused retention, while existing protected interactions take precedence and are removed when protections end under capacity fallback.

The timer policies are **per element**, not scroll-container directives. The service uses an elapsed-time clock and reports state/remaining time. It polls only while there are retained elements, and releases its timer on the last removal or `destroy()`.

## Identity, source evidence, selection

A retained row uses an internal **presentation identity** distinct from its canonical source identity. This prevents a new process with a reused PID or new verified generation from silently inheriting the old row's selection, copy button or provenance. The retained row still displays and copies the original authoritative snapshot. The live source getter does not include it.

The semantic layout authority validates each retained snapshot as part of the **presentation projection** while the current source remains separately authoritative. A clear visual retirement marker distinguishes snapshot from current observations. Pending selection-aware mutations from 14.7.2 are moved to the retained presentation and safely flushed upon selection release. Any active selection is never remapped to a new record.

Rows that are not protected may be removed immediately or after policy-controlled retirement. The list scroll host preserves a visible anchor when removal changes geometry. The floating preview closes when its retired source presentation is actually removed.

## Verification

```powershell
python -m tools.mcel_patch14_7_3_retirement_frob
```

The Frob uses offline Task Manager semantic fixtures with normal/stressed records at 390px and 1440px. It exercises initial removal, native browser text selection, protection beyond the deadline, all three timer policies, offscreen immediate removal, source-ID reuse, archived copy integrity, selection-aware updates pending before retirement, and layout revalidation. It does not execute Task Manager operational actions or promote production behavior. The surrounding 14.5–14.7.2 tests remain necessary regressions.

## Not covered by the initial increment

No cross-document persistence, multi-tab retained archive, source disappearance proof under incomplete observations, server-side lifetime tracking, or production Task Manager promotion is claimed. `retain` deliberately requires an external removal request; the service's `end(id, reason)` can release it. Further work can add explicit user dismissal affordances, richer visibility occlusion detection, and nested-host negotiations before general deployment.
