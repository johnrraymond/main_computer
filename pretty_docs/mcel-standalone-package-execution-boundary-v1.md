# MCEL Patch 17 — Independent package-host execution boundary

This is the first **package-owned presentation** migration probe. It is **not**
`fullSemanticRuntimeProven`, does not replace the production host-bound projection,
and does not borrow a legacy browser page to claim independent execution.

## Why the previous certificate is insufficient

Calculator and Code Editor currently declare `presentationAuthority: existing-host-html`
and host-facade `runtimeMethod` names. The DSL contains a canonical recursive
layout tree and intent identities, but not portable, executable implementations
of those domain operations. Rendering a second copy of the host's HTML/CSS would
not establish independent presentation. Invoking the host facade in a different
page would not establish independently authored behavior.

**Invariant:** A package-host certificate must never claim an intent was executed
unless the generated package contains and runs its authoritative implementation
and independent observation verifies the effect. Unimplemented intents always
refuse execution; they are not stubs with a success receipt.

## What the new probe generates

`python tools/mcel_standalone_package_probe.py --build-only --json`

For each existing DSL-authoritative app, the generator compiles the current
`application.js`, validates its canonical layout tree and surface, derives the
existing generated layout/surface/intent modules, and writes:

```
runtime/build/mcel/standalone-packages/{app-id}/
  index.html                # independent minimal host document
  standalone.css            # generic structural CSS, not host-authored CSS
  standalone.js             # generic declarative rendering/refusal runtime
  presentation.json         # source/projection digests and missing intent inventory
  contracts/layout.js      # derived from the app DSL
  contracts/surface.js
  contracts/intents.js
```

None of these generated assets are tracked as source changes. No real
Calculator or Code Editor source HTML/CSS is imported, copied, referenced, or
moved into the package. No production `mcel_apps/` or app host route is mutated.
The independently generated page builds region nodes from the compiled tree,
applies only grid templates that retain every declared child, and creates
**disabled** controls for intents whose portable implementation is absent.

The package page exposes `window.McelStandalonePackageHost` with explicit
`structuralVerified` and `fullSemanticRuntimeProven` fields. Calling `dispatch()`
on an unavailable intent throws `MCEL_STANDALONE_REFUSED`. The module does not
publish any write receipt or take a filesystem action.

## Browser and certification behavior

On a machine with Playwright Chromium:

```
python tools/mcel_standalone_package_probe.py --structural-only --json
python tools/mcel_standalone_package_probe.py --json
```

The first command returns zero **only if** independent page rendering passes its
fresh Chromium structural checks at 1140/750px Calculator and 1220/1050px Code
Editor: exact declared node inventory, generated controls all disabled,
explicit intent refusal, document horizontal containment, requested viewport,
no outside-package asset requests, and integrity-checked projected sources.
This is a structural *presentation* observation, not visual equivalence with
the host-bound application. The second command deliberately exits nonzero,
even after a structurally successful browser run, because portable behavior
implementations are still missing.

The report schema `mcel.standalone-package-execution-report.v1` has independent
`structuralPassed` and `fullSemanticRuntimeProven` fields. It refuses a semantic
PASS for either current app. An unavailable or policy-blocked browser also
refuses structural PASS; `--build-only` does **not** count as browser evidence.

A machine-readable report may be saved with `--output <path>`.

## Remaining migration before full package-host truth

1. Define a portable, package-owned implementation contract in the DSL/IR for
   local state transitions and external capability boundaries. Never infer
   executable methods from the existing `runtimeMethod` string.
2. Implement Calculator deterministic operation authority without invoking its
   host facade. Independently verify results and states for all declared intents.
3. Move Code Editor's read/save/review/apply transactions to independently
   provisioned, explicit capabilities with authorization, expected-source-hash,
   and receipt verification. Never use a UI shim to approve or perform writes.
4. Add an acceptance comparison against the same operation fixtures and the
   existing host-bound proof, including negative/stale-write cases.
5. Only then promote package-host semantic truth, after independent Chromium
   evidence and a repository-bound provenance check.

The existing host-bound layout + intent certificate remains valid in its original
scope and is not modified by this exploratory standalone projection.
