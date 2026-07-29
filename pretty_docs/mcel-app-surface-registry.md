# MCEL App Surface Registry

Patch 19 adds a small registry that separates apps with required MCEL
app-surface conformance from apps that are still legacy or not converted yet.

The registry is intentionally policy-only. It does not rewrite apps, add panels,
or make unsupported apps look broken. It declares which conformance layers are
currently required for each app.

Required app-surface entries:

```text
file-explorer    semantic-runtime
document         semantic-runtime
website-builder  semantic-runtime
code-editor      semantic-runtime
calculator       semantic-runtime
mcel-lab         semantic-runtime
git-tools        semantic-runtime
```

File Explorer, Document Editor, and Calculator require the full five-layer baseline because they now have
domain-neutral semantic surface contracts:

```text
semantic-surface
layout-grammar
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

Git Tools is now promoted to semantic-runtime after its governed-publish domain adapter proved full intent-level semantic coverage. Its current policy still requires the runtime conformance layers because the app's semantic proof comes from the domain adapter plus acceptance evidence, not from a static surface extraction requirement:

```text
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

That distinction matters. An app can be semantic-runtime without requiring every static semantic/layout layer in the FLOG scenario policy, as long as the truth audit binds runtime proof, acceptance evidence, and adapter readiness exactly.

Other legacy apps remain declared as not-required so diagnostics and tests can
distinguish:

```text
legacy / not converted yet
  from
surface-aware app failed conformance
```

The runtime conformance summary includes registry fields:

```text
conformanceRequired
registryState
registryPolicy
requiredLayerIds
policyFailedLayerIds
policyUnavailableLayerIds
```

That gives the copied diagnostics payload enough context to tell whether a
failure is a real conformance break or simply an app that has not been enrolled
yet.
