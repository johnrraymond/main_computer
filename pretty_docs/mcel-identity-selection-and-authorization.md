# MCEL identity selection and transparent authorization — governing UI contract

**Status:** Implemented in the MCEL selection and local authorization interface on November 13, 2026; live Windows acceptance and any remaining historical-oracle migration are pending. This document supersedes earlier MCEL/SLH instructions that make **Login Administrator**, **Logout Administrator**, or **Sign in to load Coolify targets** mandatory user-interface steps. It does **not** supersede the existing ORG-SLH cryptographic challenge, session, role, enrollment, root-grant, or TLS security protocols.

## Governing model

**Selecting an identity is not logging in.** Identity selection does not initiate login, confer administrator rights, or grant a session. MCEL offers one explicit active identity per scoped management context. The identity belongs to the LMS-SLH local signer registry; it is not an ORG-SLH enrollment, an administrator grant, or a persistent server session. Selecting an identity makes it available as the signer for operations directed to the chosen, authenticated target. Selection itself must not issue a login request, grant rights, enroll an identity, bootstrap an organization, or start the ORG-SLH service.

An operation requiring proof invokes the selected signing capability through the existing authenticated transport. That transport may transparently perform an ORG-SLH challenge/signature exchange, obtain or refresh a short-lived target-bound session, or sign a request. Those are **protocol implementation details**, not a second user-facing login workflow. ORG-SLH remains the only authority for public-key enrollment, active/revoked status, roles, and access policy. Denial stays denial. Repeated selection never constitutes administrator enrollment or authorization.

This applies consistently to **This Computer**, **Network SLH**, and **Remote SEH**. A selected target and a selected signer are independent dimensions. Remote SEH can relay only its existing restricted identity-management API, not Coolify, deployment, lifecycle, or unrestricted ORG-SLH administration. Switching target does not transfer an organization session or administrator status.

## Identity list: selection, deselection, and roles

- **Select:** Clicking an available identity makes it active in the current target context, with a visible selected indication. No additional **Login**, **Sign in**, or **Logout** action is requested. Selecting an identity does not itself cause a privileged network operation.
- **Deselect:** The identity list must provide an explicit, discoverable **Deselect identity** action (or a **None** entry). Clicking the already selected identity may also deselect it, but that is not sufficient as the only affordance. The resulting state is **no active identity**. This is not Delete, Revoke, Deactivate, or removal of the installation's persisted default identity.
- **No automatic re-selection:** After the operator explicitly deselects, the browser must not immediately select the installation default, first row, last signer, or a prior target's identity. The explicit empty selection is honored for the current management context, including its reload/restore rules; any later return to default must be an explicit operator action or a separately documented, non-conflicting policy.
- **Administrator indicator:** Every listed identity for which the **selected ORG-SLH authority** has verified an active administrator role must visibly show **Administrator**, including identities that are not currently selected. This information must also appear in an accessible text label/description, not only as a color or icon.
- **Role provenance:** The admin marker comes from verified, target-scoped organization enrollment/role data; it never follows from a local identity's name, fingerprint, possession of a private key, or `DEFAULT` flag. A revoked/removed identity must not retain an active Administrator marker. The same signer may have administrator rights on one SLH and none on another.
- **Unknown is not non-admin:** If role information cannot be obtained under the existing security policy, show a **Role not verified** or equivalent unknown state; do not invent administrator status or incorrectly label the identity as non-admin. Neither the badge nor a cached role record is proof of authorization for subsequent operations. The server rechecks the principal and role on every protected action.
- **No unnecessary disclosure:** Identity listing does not authorize returning all organization enrollment records to unauthenticated parties. An administrator-rights indicator may be shown only when its source is lawfully accessible through the existing local trust boundary or verified target capability. No extra public endpoint should disclose private organizational membership merely to render a badge.

The list must distinguish **local signing identities** from the **Organization Identities** notebook, which displays ORG-SLH's enrolled device records. The notebook's Pending, Enrolled, and Trash operations remain consequential and independently authorized.

## Authorization lifecycle and state isolation

| Event | Required MCEL behavior | Invariant |
| --- | --- | --- |
| Identity selected | Set the active local signer for the current target; show its selection and any verified role indicator | No login, bootstrap, root grant, or privilege mutation on selection |
| Authorized read requested | Use selected signer for transparent proof/session as needed, then fetch under ORG-SLH policy | No separate sign-in button; no access without proof and role |
| Authorized mutation requested | Perform existing challenge/signature/session protocol and enforcement; retain explicit confirmation where required | Selection alone never authorizes the mutation |
| Identity deselected | Clear signer binding, target-scoped credential/ticket handles, protected cached data, pending auto-fetches, and role-dependent controls; display **No identity selected** | No silent fallback to default and no globally revoked/deleted identity |
| Identity changed | Dispose the previous signer/target session and stale results before using the new signer | No cross-identity bearer, result, or audit attribution reuse |
| SLH target changed | Invalidate target-specific sessions/role proofs and refresh status only for the new target | A local/admin role never propagates to another SLH |
| Session expires or key is revoked | Clear invalid transport authority; retry transparent authentication only when valid and required; show refusal if unavailable | Do not ask for a manual login or auto-enroll/promote |
| Role lookup unavailable | Keep role **unverified**; do not mark administrator | Fail closed without falsely displaying unprivileged or privileged status |

Deselect and identity changes cannot retroactively cancel a mutation already committed by ORG-SLH; they must prevent *new* operations using the stale identity. An ongoing consequential operation remains tracked by its original identity and operation receipt, even if the UI no longer presents its former identity as selected. Short-lived protocol credentials must never appear in the identity list, semantic MCEL state, browser storage, logs, or public observations.

## First administrator and recovery are separate operations

There is **no automatic first-administrator enrollment, root-grant, or bootstrap on identity selection, list loading, application mount, or target change**. The existing bootstrap-only `LOCAL-AUTH-KEY` path remains constrained by ORG-SLH atomic zero-administrator checks. If a new organization requires establishment or an existing organization requires administrator recovery, MCEL must expose an **explicit, appropriately confirmed setup/recovery operation**, not hide a privilege grant inside selection or a transparent read.

A local ordinary identity can be selected without enrollment; lack of administrator rights must be presented as an authorization fact, not as a demand to log in. An authorization error may offer the approved request-enrollment or administrator-recovery workflow where appropriate, but cannot silently elevate privileges.

## SEH Lifecycle and Coolify consequence

The LMS is the owner of Coolify identity-state reads and the SEH harness lifecycle. See [LMS SEH Management and Deployment Authority](lms-seh-management-and-deployment-authority.md). Target selection determines the access path **before** any authentication decision:

- **This Computer:** MCEL calls the local LMS through its protected local management interface. Reading the saved Coolify controllers, selected binding, and deployment status requires **no ORG-SLH login, administrator bearer, chosen signing identity, or Worker Bank observation**. Mutations still require local-machine authorization, confirmation, and harness safety gates. The browser receives sanitized metadata only.
- **Network SLH:** MCEL connects to the explicitly selected ORG-SLH using direct, pinned TLS. Only that ORG-SLH authenticates the selected signer and authorizes the specific operation before forwarding the bounded request to **its local LMS**. The remote LMS reads its own verified Mother registry, selects its own Coolify controller, and owns any deployment job. No local fallback or transfer of a different target's bearer is permitted.
- **Remote SEH:** Cannot read Coolify or invoke LMS deployment operations, including through the restricted identity-administration relay.

There must be **no dedicated Sign in to load Coolify targets** control. A local denied read must report failure at the local LMS boundary, not `authority-denied` because a local ORG-SLH session does not exist. Remote denial must distinguish authentication, authorization, and connection/target errors; a failed read never becomes a fabricated empty controller list. Deselecting an identity clears any **identity-scoped remote** authorization and protected data; it does not revoke local OS management authority or prevent a permitted local LMS read. The local/remote distinction is not an application-specific exception; it follows the general MCEL management-target authority model.

## Required oracle and acceptance checks

The MCEL semantic source, generic identity-selection projection, session adapter, and affected SLH/SEH oracles must test all of the following:

1. Selection alone produces no login request, privilege grant, bootstrap, or service start; a protected read can authenticate transparently when requested.
2. Deselect is visibly available; leaves **no identity selected**; does not delete/revoke or silently reinstate the installation default; disables protected controls and clears stale data/tickets.
3. Every verified administrator in the list displays **Administrator** for the selected organization, even if unselected; revoked, differently scoped, and unknown-role identities are represented accurately.
4. Changing identity or target never reuses another identity's token, TLS pin, organization data, role decision, or pending privileged response.
5. Explicit initial enrollment/root recovery remains protected by its independent affirmative authorization; nothing in identity selection silently invokes it.
6. The SEH Lifecycle saved-Coolify dropdown reads directly from local LMS on This Computer without an ORG-SLH authentication step, and through the remote ORG-SLH → LMS forwarding path on Network SLH; no dedicated sign-in UI, no bypass of remote administrator checks.
7. These semantics are general MCEL identity behavior, not a special CSS/layout or hard-coded Calculator/SLH/SEH exception; existing network, identity, and SEH authority tests continue to pass.

**Implementation note:** The MCEL SLH semantic UI no longer declares Login/Logout intents. A protected operation can invoke the existing challenge/signature protocol through an internal `authenticate` action. An Administrator label is shown only after a target-authorized enrolled-record read; otherwise the role is unverified. Historical browser oracles that explicitly require Login/Logout may still need migration. The underlying backend authentication and session endpoints remain in place.

## Relationship to prior specifications

This contract updates the **presentation and workflow layer** in [LMS-SLH Identity Ownership](lms-slh-identity-ownership.md), [MCEL Unified Administration V4](mcel-slh-unified-admin-oracle-v4.md), [MCEL Organization Identity Notebook V1](mcel-slh-identity-notebook-oracle-v1.md), [ORG-SLH Identity Notebook Backend](slh-organization-identity-notebook-backend.md), and [SLH Backend Current Capabilities](slh-backend-current-capabilities.md). Older `Login Administrator`/`Logout Administrator` descriptions are historical rather than governing UI requirements; the backend protocol remains available to existing internal clients. The existing [ORG-SLH Root Authorization](slh-local-auth-key-root-authorization.md) and [Phase 2A Authentication API](slh-phase2a-authentication-api.md) remain authoritative for backend authentication and privilege checks.
