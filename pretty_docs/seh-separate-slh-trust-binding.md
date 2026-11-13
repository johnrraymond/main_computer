# SEH infrastructure deployment and SLH trust enrollment

> **Legacy recovery-only procedure; not the normal deployment workflow.**
> The SLH must eventually own provisioning and activate its existing resident
> auto-connection supervisor. This standalone operation does not complete that
> workflow. Use explicit trust replacement only after inspecting existing
> identity and authorizing rotation; never create a replacement key by default.

The `deploy-seh` operator action only manages infrastructure. It does not
read local SLH keys, SQL tables, or automatically change remote trust.

After an SLH signing identity is deliberately created or rotated, explicitly
bind it to the already registered SEH using the independent guarded operation:

```powershell
python .\seh_mutate_harness.py bind-slh --network mainnet --seh company-primary --host coolify-c
```

The first invocation is read-only and prints the exact mutation command.
When an older trusted public key already exists, the operator must explicitly
approve changing that trust anchor:

```powershell
python .\seh_mutate_harness.py bind-slh --network mainnet --seh company-primary --host coolify-c --replace-existing-trust --execute-mutations --yes-i-know-this-mutates-seh
```

`bind-slh` takes the existing connector's **public** P-256 signing identity
from `runtime/state/slh` without creating a new private key. It verifies the
selected Mother infrastructure identity, Coolify controller, registered
application UUID and ownership marker, and the existing remote SLH signing public key. Duplicate trust records,
masked trust, and unauthorized key replacement are rejected. The historical
SLH association label is not a relay routing field or a trust credential.

The operation updates the `SEH_SLH_PUBLIC_KEY_B64` trust environment variable,
verifies their readback, and triggers redeployment. A queued deployment is
not proof of authenticated WebSocket readiness. This legacy operator does
**not** start a connector or configure a running SLH's managed gateway; use
independent `/readyz` and authenticated-tunnel verification before claiming
operational success. Do not start a competing standalone connector.

The connector's private key never leaves local SLH state. No SQLite database
or migration is part of this flow. The operation does not change Pinky Promise
pricing, wallet accounting, marketplace work, or the SEH's infrastructure
identity. If Coolify masks the updated environment values on readback, the
harness refuses to trigger deployment and reports that verification is still
needed; do not assume a successful binding from a partial mutation.
