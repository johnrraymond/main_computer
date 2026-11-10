# Secure Local Hub — First MCEL semantic slice

Authoritative operator workflows are in `pretty_docs/secure-local-hub-administrator-manual.md`; backend truth is in `pretty_docs/slh-backend-current-capabilities.md`. `application.js` declares the executable semantic intents and states. MCEL synthesizes the workspace. This is a **local MCEL semantic application**, with identity discovery and a bounded, user-initiated **Login Administrator** workflow. For a truly unestablished local organization, that click performs protected first-administrator bootstrap, safe loopback configuration if necessary, ORG activation, and signed login without asking for an Admin ID/Name/P256 form. An established organization never grants an additional administrator merely because login was attempted. This is not evidence of a running service or production-host acceptance. Login/Logout availability is supported by bounded, read-only refreshes of the local-service overview, available identities, and session state on authentication transitions and while the browser page is visible. A successfully signed-out session must expose Login Administrator again; refreshing identities must preserve any still-available operator selection. These observations never authorize an administrator grant or start an SLH service.

The scaffold compatibility files under `contracts/` and `src/` are **inactive placeholders**; the canonical route is generated directly from the compiled JavaScript and its verified composition. No hand-authored HTML controls, worker values, or visual layout are authority.

```mcel-app
id: slh
title: Secure Local Hub
status: implemented-first-slice
current_runtime_status: structural-only
primary_user_goal: Understand real SLH availability without assuming that missing evidence means zero Workers.
```

```mcel-use-case
id: slh.use-case.observe
app: slh
user_goal: See whether the resident loopback service can be verified, then inspect its observed initialization and organization states.
acceptance: A real observation populates derived state with provenance; an absent service remains UNREACHABLE without side effects.
```

```mcel-region
id: slh.region.generated
app: slh
role: generated-application-surface
responsibility: Compose MCEL-authored status observations; no developer-authored geometry or widget layout.
```

```mcel-requirement
id: slh.requirement.nonfiction
app: slh
type: safety
requirement: Never fabricate a Worker or infer an empty fleet from an inaccessible authenticated inventory.
acceptance: Unauthenticated worker inventory remains AUTH_REQUIRED or NOT_OBSERVED with an empty unverified display.
```

```mcel-intent
id: slh.intent.refresh-overview
app: slh
risk: external-read
requires: Already-running local service, or a truthful unavailable result.
produces: Typed, source-attributed observation.
```

```mcel-acceptance
id: slh.acceptance.first-slice
app: slh
requires:
  - Valid generated composition without authored layout or HTML binding
  - No startup, control mutations, credentials, or admin authority
  - Unreachable and invalid local SLH service fail closed
  - Native canonical Overview and Worker Bank links
```

```mcel-finding
id: slh.finding.scope
app: slh
status: first-slice-only
finding: Worker inventory requires a later authenticated read boundary, and actual admin operations remain deferred.
```
