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
git-tools        runtime-baseline
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

Git Tools is enrolled as a runtime-baseline app-surface entry first. Its current policy requires only the runtime layers while the governed-publish adapter remains scope-limited and does not yet claim full semantic-runtime coverage:

```text
runtime-ownership
runtime-visual-fit
diagnostic-no-throw
```

That distinction matters. An app can be required for conformance without already
having full static semantic/layout extraction. The registry prevents those two
states from being confused:

```text
required runtime baseline
  is not the same as
full semantic-runtime conversion
```

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
