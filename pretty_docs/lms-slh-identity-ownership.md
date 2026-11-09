# LMS-SLH Ownership of Unified Local Identities

**Status:** Governing architecture and lifecycle contract, revised November 8, 2026. Distinguishes **current backend capabilities** from **next implementation requirements**. Automatic default identity creation is a requirement, not a claim about deployed code.

## Governing invariant: one kind of identity

**There is no machine-versus-user identity distinction in Main Computer.** An identity is one locally held cryptographic principal, with the **same data model, key-generation/signing mechanism, persistence/lifecycle APIs, and eligibility for enrollment** regardless of whether it is used by a person, an unattended process, a Worker, or the default Main Computer installation. LMS-SLH owns every such local identity. An actor's description, OS custody context, display label, or operational purpose **does not define a separate kind of identity or confer different cryptographic authority**.

Every Main Computer installation must have a **default identity**. That means one identity chosen/created automatically for that installation on LMS-SLH initialization; it is **not** a privileged identity type, machine-only key format, different authenticator, or different approval system. Additional identities use the exact same model and lifecycle. An identity can represent an installation in one context and a person or another actor in another; its capabilities come from the same cryptography, while any SLH organization's roles and permissions come solely from **explicit organizational enrollment and policy**.

Storage locations and OS-user access restrictions are **key-custody policies**, not a split into machine and human identity systems. An installation's default must remain stable and scoped to that installation; a user must not gain access to another user's private key merely because both identities share one type. No identity grants administrative authority merely by being present or being the default.

## The two SLH services

The **Local Management Service for Secure Local Hub (LMS-SLH)** is the *frontend/local-management SLH service* shipped with each Main Computer installation. It operates locally and owns the **unified identity lifecycle** for this installation and identities used by its local actors. It is not the browser viewport or an automatically created organization. Its local Authenticator API is accessible only through the authorized loopback/local-user boundary. Availability of LMS-SLH does not mean that an SLH organization exists, that the Organization Service is running, or that any identity has an administrator role.

The **backend SLH Organization Service** is the separately configured/activated organization authority. It **does not create, store, or own local identities' private signing keys**. It keeps *public-key enrollment, organization roles, status/revocation and authorization* records and authenticates a presented identity through cryptographic challenge. It can deny any locally held identity, including the default. Its own service signing/TLS credentials are a separately held trust relationship for connecting to that endpoint, **not evidence of local administrator enrollment**.

The **MCEL frontend** presents identity discovery, selection, creation where authorized, and organization setup/login. It is a client of LMS-SLH. Private keys never enter application JavaScript or an Organization Service API response. The general viewport server (`127.0.0.1:8765`) is **not** an identity manager.

## Identity ownership and state boundaries

| Entity | Owning component | Persistence / scope | Relationship to organization authorization |
| --- | --- | --- | --- |
| **Any LMS-SLH identity** (installation default, person, Worker or other actor) | **LMS-SLH** | **One identical identity model and lifecycle**; managed in authorized local storage with appropriate custody/access restrictions | May be unenrolled or explicitly granted roles; its label, use, or origin gives no privileges |
| **Default identity selection** | **LMS-SLH** | **[TARGET]** Exactly one persistent default per installation; a regular identity selected/created at first startup, not automatically shared between installations | Default status is *not* an identity class and grants no administrator authority |
| Organization Known Device / administrator enrollment | **Backend SLH Organization Service** | Durable records of public keys, fingerprints, users/roles, status and revocation | Grants authorization only after its own bootstrap/enrollment policy and valid authentication |
| Organization service identity and TLS serving credential | SLH organization service/control authority | Protected service state, separate from user/installation credentials | Identifies the service to clients; not a personal administrator identity |
| Authenticated organization session | Backend SLH Organization Service | Short-lived, scoped, revocable | Issued only following accepted proof-of-possession and role checks |

**Ownership is of the unified identity lifecycle and its APIs, not unrestricted access to secrets.** LMS-SLH mediates generation, public metadata, listing, private-key use for authorized signing, persistence, protection, and explicit recovery/rotation for **every identity by the same rules**. Operating-system user boundaries must still be enforced: a privileged service account must not silently impersonate a local user, expose another user's keys, or silently merge private credential stores. Storage scope changes who can use a key, **not what kind of identity it is**.

Every LMS-SLH identity is a **local cryptographic principal**, not an organization enrollment, administrator role, or marketplace wallet key. A default identity identifies its installation by convention but has no different cryptographic structure or entitlement. Service restarts must not replace existing identities. Corrupt, conflicting or inaccessible material must yield a diagnosable failure and explicit recovery rather than silent overwrite or rebootstrap.

## Intended startup and application workflow

1. **LMS-SLH starts or is explicitly started on demand.** It opens its authorized local identity registry, including the installation's default-selection record and any identities accessible in the current OS-user context. There is **one identity model**, regardless of key custody scope. This does **not** require enabling the backend Organization Service. On this development machine it does not install or enable an automatic Windows boot task.
2. **LMS-SLH checks the default identity.** **[TARGET — NOT AUTOMATICALLY IMPLEMENTED]** If the installation has none and its persistent state is demonstrably new, generate exactly one ordinary LMS-SLH identity and mark it as this installation's default. Otherwise reuse the existing identity and default selection. Concurrent starts cannot duplicate it; missing/corrupt identity data in a nonfresh or migrated store fails closed pending reconciliation.
3. **LMS-SLH lists other available identities using the same API.** Additional identities have exactly the same cryptographic structure and creation/signing capabilities. The UI discovers and selects an accessible identity rather than asking for an internal Device ID. Local OS-user custody restrictions still govern key use.
4. **MCEL observes public identity status**, then examines the chosen local or network SLH *separately*. The **This Computer** tab can use the local identity for first-admin bootstrap **only when the backend reports a genuinely unclaimed organization**, with the required physically-local authority and explicit user consent. If the organization already exists, do not bootstrap again.
5. **Backend SLH checks enrollment and authenticates.** Any LMS-SLH identity might be unenrolled, enrolled with a non-admin role, revoked, or a valid administrator. LMS-SLH uses the private key to sign the backend's fresh, appropriately scoped challenge; backend SLH validates enrollment and role, then issues a session. An unknown local key never becomes an administrator merely because it was generated at startup.
6. **For Network SLH**, the same local identity custody remains with LMS-SLH on the client computer, but remote organization trust/authorization is a different target. Verify the remote SLH identity before authentication; do not reuse a local administrator session or silently enroll the identity remotely.

## Current implementation and explicit gap

**[IMPLEMENTED BACKEND]** `main_computer/slh/local_service.py` attaches `LocalAuthenticatorAPI` to the loopback LMS-SLH service. Its `LocalIdentityStore` is in `main_computer/slh/authenticator_store.py`. Currently available local endpoints include:

| HTTP endpoint on LMS-SLH (`127.0.0.1:8897`) | Purpose |
| --- | --- |
| `GET /v1/local/authenticator/status` | Reports available credential type and storage limitations |
| `GET /v1/local/authenticator/identities` | Lists local identities and invalid entries in the process's permitted user context |
| `POST /v1/local/authenticator/identities` | **Explicitly** creates a local P-256 identity using a supplied `device_id` |
| `GET /v1/local/authenticator/identities/{device_id}` | Retrieves public identity metadata and public key |
| `POST /v1/local/authenticator/identities/{device_id}/sign` | Signs a validated challenge without exporting the private key |

**[CURRENT LIMITATION]** The store is currently user-local (`LocalIdentityStore` resolves `user_identity_dir()` or a guarded legacy directory), and its API reports `protection: none`: private keys are presently unencrypted PEM with file/ACL safeguards, **not** hardware-backed protected credentials. LMS-SLH does **not yet** have an accepted, idempotent **default installation identity** initialization path or an accepted cross-user identity broker. A locally enrolled administrator record may therefore coexist with an unverified/missing local credential. Do not document these future behaviors as deployed.

**[TARGET IMPLEMENTATION]** LMS-SLH creates or reuses one **ordinary identity** as the installation's default when its local service initializes. Persist the default association under installation-private state and protect the corresponding key according to its custody/access policy. **Do not create a special machine identity type, a second user identity system, or a separate cryptographic authority.** Identity records and APIs must remain unified; any storage separation is only for ownership/access protection. Default creation has no side effect on organization configuration, role grants, remote discovery, wallet authority, or network listener exposure. The frontend consumes this result; it does not implement another key store or generator.

## Required acceptance criteria for the next identity patch

- Fresh installation: exactly one default **ordinary LMS-SLH identity** from LMS-SLH initialization, even with concurrent startup; same public key after restart.
- **Uniformity:** no machine/user identity discriminator, separate cryptographic schema, signing rules, role eligibility, or creation API. Labels, purpose and default selection must not be treated as cryptographic roles.
- Separate installations: different identities by default, even on the same physical computer.
- All existing identities, regardless of label or actor: preserved as the **same class of credential**; no implicit duplication, conversion, replacement, migration, or merging of protected stores.
- Damaged, ambiguous, or inaccessible identity state: explicit failure, no automatic replacement.
- Multiple OS users: identity listing/signing respects user-context boundaries; no cross-user private-key disclosure or privilege escalation.
- LMS-SLH offline from backend SLH: creation, discovery, selection and signing of any local identity do not depend on organization enrollment or network connectivity.
- Backend SLH established/active with no local admin key: no implicit admin creation, key substitution, or restart of the first-admin bootstrap.
- Backend challenge fails, identity is not enrolled, or role is insufficient: no administrative session.
- Network SLH: target binding, pinned trust and authorization remain independent of the local SLH organization.
- Starting the frontend or LMS-SLH does not auto-start the backend Organization Service or install a Windows-boot SLH task.

See the [Administrator Manual](secure-local-hub-administrator-manual.md), [Backend Current Capabilities](slh-backend-current-capabilities.md), [Device Authentication Architecture](slh-seh-device-authentication-architecture.md), [Phase 2A Authentication API](slh-phase2a-authentication-api.md), and [MCEL SLH Oracle V3](mcel-slh-organization-observation-oracle-v3.md).
