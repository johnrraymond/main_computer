# SLH managed workers: integrated Phase 2B + 2C backend

Status: **backend experimental**. This is infrastructure, not a finished UI or an SEH implementation.

## What works

- An already-enrolled Worker device can open a long-running **TLS-pinned** WebSocket to an SLH on port **8898**. No worker marketplace wallet or organizational CA is required.
- The client generates a true Known Device P-256 signature in response to the SLH challenge. Device must be enrolled as a `worker`; unknown/administrator identities are denied.
- The SLH stores worker capacities and models, takes exclusive per-device reservations, forwards the original Hub `hub.work.offer` followed by `hub.work.authorized` only after the upstream Hub has committed the hold, and forwards the result to its original Stable Hub live session. No worker ever receives the MSK or marketplace private key.
- A single SLH-managed wallet and issued MSK may represent several independent public worker sockets. The Hub owns public connection IDs; one local disconnect closes only its own upstream socket.
- Managed mode is wired into the current Worker backend runtime settings (not the browser configuration UI yet). Its live-session execution callback is reused.

## Security boundaries

SLH port **8897** remains the loopback administration API. Port **8898** is a separate TLS-only worker gateway; do not expose port 8897 to LAN or SEH. First enrollment still uses administrator approval via the Phase 2A API. Workers receive only their own P-256 authenticator key; never copy marketplace wallets, MSKs, or SLH transport private keys to worker machines.

The SLH creates a self-signed **transport-only** TLS leaf (not a signing CA) on first start and prints its SHA-256 certificate fingerprint. An administrator must verify/distribute that fingerprint out of band before workers connect. Changing the SLH leaf requires re-pinning clients; automatic rollover is not implemented. Configure firewall/network reachability before using LAN bindings. No certificate enrollment/revocation service is introduced by this patch.

This is a scoped first implementation. No phone app, SEH tunnel, private B2SelfB debit, multiple organization wallets, marketplace payout bridge, UI management, or online-enrollment transport is provided here.

## Local SLH gateway

Install dependencies: `python -m pip install -r requirements.txt` (or `python -m pip install 'websockets>=13,<17'`).

With the organization already initialized at the canonical state directory:

```powershell
python -m tools.slh_managed_gateway fingerprint
python -m tools.slh_managed_gateway serve --host 127.0.0.1
```

For an administrator-approved LAN interface, use `--host <existing LAN IP>`; this binds only the managed TLS socket, **not** the loopback API. Start the ordinary `python -m tools.slh_auth_harness serve` separately for local administration if required.

## Configure a managed Worker (on its own machine)

Enroll its identity through the existing SLH administrator approval API, then create/retain the P-256 identity on that Worker machine. Once the SLH fingerprint has been verified, preview settings:

```powershell
python -m tools.slh_managed_worker_config `
  --device-id worker-office-01 `
  --slh-url https://10.0.0.10:8898 `
  --fingerprint <verified 64 hex SHA256 fingerprint> `
  --identity-dir "$env:LOCALAPPDATA\MainComputer\authenticators"
```

Add `--apply` to update only the selected managed settings via the existing locally running Worker viewport HTTP settings endpoint. Managed Worker runtime still follows the normal sellerEnabled, local AI capacity and idle/busy policies; the Worker application UI does not yet expose this connection mode and may overwrite it if the user selects a different network. Use the existing runtime sync endpoint or supervisor to trigger connection.

## Enable public marketplace proxy (explicit opt-in)

Keep a wallet key only on the SLH machine. The wallet file is an **existing marketplace wallet** secret, separate from the P-256 authentication keys. Never place it on Worker PCs. For local dev:

```powershell
python -m tools.slh_managed_gateway serve `
  --host 127.0.0.1 `
  --enable-marketplace --network dev `
  --wallet-file C:\secure\slh-marketplace-wallet.json
```

Supported upstream profiles: `dev`, `test`, `testnet`, `mainnet` (using `main_computer.hub_networks`). Live networks require the additional `--allow-live-marketplace` flag. The SLH asks the actual Stable Hub for a wallet-signed MSK; no MSK is fabricated. Upstream connections appear only after a real managed worker connects.

## Acceptance and limitations

Run `python -m unittest tests.test_slh_managed_gateway -v`. Tests exercise the real TLS-pinned listener, device P-256 challenge, known worker admission, unknown identity denial, exclusive reservations, local authorized execution, and two simultaneous Stable Hub live sessions sharing one wallet-derived account. They do **not** claim an end-to-end paid requester charge or an external-network deployment. This boundary is deliberate; do not use these results as evidence of escrow/settlement correctness until a paid job has been run through the complete SLH bridge.
