# Cloudflare hidden-origin mail worker runbook

Status: operator runbook for the staged `tools/cloudflare_mail_worker.py` flow.

This document records the design and current deployment state for the
`greatlibrary.io` hidden-origin inbound mail path. It is intentionally written as
a handoff document between the generated Cloudflare artifacts and the scripted
Coolify deployer so the operator does not need to run random one-off commands.

## Goal

Receive inbound mail for `greatlibrary.io` without exposing the origin server as
the public MX target.

The target path is:

```text
remote sender
  -> Cloudflare Email Routing MX
  -> Cloudflare Email Worker
  -> HTTPS POST to mail-ingest.greatlibrary.io
  -> Coolify mail-ingest service
  -> Maildir-compatible mailbox storage
```

The public internet sends SMTP to Cloudflare. The origin server receives only
authenticated HTTPS ingest requests from the Worker.

## Account-system roadmap

This runbook covers the working hidden-ingest pipeline. The larger product plan
for signup-driven Great Library email accounts is documented in:

```text
pretty_docs/great-library-email-account-system-plan.md
```

That plan is the source for the next implementation phases: user registry,
passwords, aliases, known-user delivery, webmail, public signup, outbound
sending, backups, and moderation.

## Non-goals

This is not a traditional public mail server deployment.

The current hidden-ingest path does **not** expose these protocols publicly:

```text
SMTP/25
SMTP submission/465/587
IMAP/993
POP3/995
```

Those require a separate strategy: DNS-only origin exposure, Cloudflare Spectrum,
or a private Cloudflare Tunnel/client bridge. The current worker path is focused
on hidden-origin inbound delivery.

The current worker path also does not yet include the POP/IMAP reader layer. The
ingest app writes messages into a Maildir-style store so Dovecot, webmail, or a
future mailbox reader can be layered on later.

## Current checkpoint

Implemented in the repository:

```text
tools/cloudflare_mail_worker.py prepare
tools/cloudflare_mail_worker.py coolify-apply
tools/cloudflare_mail_worker.py coolify-check
tools/cloudflare_mail_worker.py coolify-discover
tools/cloudflare_mail_worker.py coolify-sync
tools/cloudflare_mail_worker.py cloudflare-guide
tools/cloudflare_mail_worker.py user-add
tools/cloudflare_mail_worker.py user-password-set
tools/cloudflare_mail_worker.py user-list
tools/cloudflare_mail_worker.py alias-add
tools/cloudflare_mail_worker.py alias-list
```

The important completed stages are:

```text
Stage 1: prepare a stable mail-worker contract and generated artifacts
Stage 2: deploy/update the Coolify ingest service from that contract
```

Still manual/config-assisted:

```text
Cloudflare Worker creation/update
Cloudflare Worker secret entry
Cloudflare Email Routing rules
Destination-address verification for Gmail forwarding
```

Not yet implemented:

```text
Automatic Cloudflare API apply
POP/IMAP mailbox reader service
Outbound sending relay
Mailbox management/admin UI
```

## Mail user registry foundation

`prepare` now also creates a registry file next to the generated contract:

```text
runtime/cloudflare-mail-worker/greatlibrary.io/mail-users.json
```

The contract references this as `user_registry_file`. Re-running `prepare` keeps
an existing registry in place so operator-created users and aliases are not
silently erased.

Initial account-management commands:

```powershell
python tools/cloudflare_mail_worker.py user-add `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --local alice `
  --display-name "Alice"

python tools/cloudflare_mail_worker.py user-password-set `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --local alice

python tools/cloudflare_mail_worker.py user-list `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json

python tools/cloudflare_mail_worker.py alias-add `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --alias a.smith `
  --target alice

python tools/cloudflare_mail_worker.py alias-list `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json
```

Passwords are stored as hashes only. `user-list` reports whether a password is
set but does not print the hash.

The deployed ingest service has not yet been switched to known-user enforcement
in this registry tranche. Apply the later ingest-policy patch before treating the
catch-all as protected from unknown-recipient Maildir creation.

## The deployment contract

Stage one writes a contract directory under:

```text
runtime/cloudflare-mail-worker/greatlibrary.io/
```

The most important files are:

```text
mail-worker-contract.json
secrets/mail_ingest_secret
worker/src/index.ts
worker/wrangler.toml
coolify/compose.yaml
coolify/mail_ingest.py
cloudflare/manual-routing-plan.md
cloudflare/worker-dashboard-paste.md
cloudflare/wrangler-commands.md
operator-commands.txt
README.md
plan.json
```

`mail-worker-contract.json` is the source of truth for later scripted stages. It
records the domain, Worker name, ingest host, ingest path, Coolify URL, routing
intent, and the relative path to the secret file.

`secrets/mail_ingest_secret` is the shared authentication secret. The Worker sends
it in the configured header, and the Coolify ingest app rejects requests without
it. Keep this file out of git, screenshots, issue trackers, and chat logs.

The secret is intentionally **not** embedded in the JSON contract. The deployer
loads it from the secret file when applying the Coolify stage.

## Stage 1: prepare

Run from the repository root in PowerShell:

```powershell
python tools/cloudflare_mail_worker.py prepare `
  --domain greatlibrary.io `
  --ingest-host mail-ingest.greatlibrary.io `
  --worker-name greatlibrary-mail-ingest `
  --coolify-url http://remote-host:8000/projects `
  --coolify-service-name greatlibrary-mail-ingest `
  --forward-local johnrraymond `
  --forward-to johnrraymondesq@gmail.com `
  --drop-local info `
  --catch-all-to-worker `
  --out runtime/cloudflare-mail-worker/greatlibrary.io
```

Expected result:

```json
{
  "ok": true,
  "contract": "runtime\\cloudflare-mail-worker\\greatlibrary.io\\mail-worker-contract.json",
  "secret_file": "runtime\\cloudflare-mail-worker\\greatlibrary.io\\secrets\\mail_ingest_secret",
  "ingest_url": "https://mail-ingest.greatlibrary.io/inbound/cloudflare-email",
  "worker_name": "greatlibrary-mail-ingest"
}
```

If the secret already exists, `prepare` reuses it. This is deliberate. Reusing
the secret prevents the Worker and Coolify app from silently drifting apart.

Only pass `--rotate-secret` when you intend to redeploy both sides with the new
secret.

## Stage 2: Coolify apply from the contract

The Coolify stage consumes the contract and secret file. It should not require
the operator to repeat the domain, Worker name, ingest path, or secret.

Dry-run first:

```powershell
python tools/cloudflare_mail_worker.py coolify-apply `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-project-name mail `
  --coolify-environment mail `
  --coolify-service-name greatlibrary-mail-ingest `
  --dry-run
```

Then deploy for real by removing `--dry-run`:

```powershell
python tools/cloudflare_mail_worker.py coolify-apply `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN `
  --coolify-project-name mail `
  --coolify-environment mail `
  --coolify-service-name greatlibrary-mail-ingest
```

The deployer accepts Coolify UI URLs such as:

```text
http://remost-host:8000/projects
```

and normalizes them to the API base:

```text
http://remote-host:8000
```

### Coolify selector options

Use names when they are unique and readable:

```text
--coolify-project-name
--coolify-server-name
--coolify-environment
--coolify-service-name
```

Use UUIDs when names are ambiguous:

```text
--coolify-project-uuid
--coolify-server-uuid
--coolify-destination-uuid
--coolify-environment-uuid
--coolify-service-uuid
```

Token sources mirror the other Coolify-centric deployers:

```text
--coolify-token-env MAIN_COMPUTER_COOLIFY_TOKEN
--coolify-token-file path/to/token.txt
--coolify-token literal-token-value
```

Prefer `--coolify-token-env` or `--coolify-token-file` so tokens do not appear in
shell history.

### What Coolify receives

`coolify-apply` renders a raw Compose service with:

```text
service/container: greatlibrary-mail-ingest
public host:       mail-ingest.greatlibrary.io
health path:       /healthz
ingest path:       /inbound/cloudflare-email
secret header:     X-Main-Computer-Mail-Secret
mail domain:       greatlibrary.io
mailbox root:      /var/mail
volume:            mail-ingest-mailboxes
```

The Compose payload includes the ingest Python app inline so Coolify does not
need extra remote files.

The dry-run output redacts the secret. The real apply injects the secret from the
contract directory.

## Stage 2b: generate Cloudflare dashboard values

After `prepare` and `coolify-apply`, use the contract to print the exact values
needed for manual Cloudflare configuration. This action does not call Cloudflare
and does not mutate anything:

```powershell
python tools/cloudflare_mail_worker.py cloudflare-guide `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json
```

By default, the shared secret is redacted. When you are ready to paste the Worker
secret into Cloudflare, run the same command with:

```powershell
python tools/cloudflare_mail_worker.py cloudflare-guide `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --show-secret
```

Use `--show-secret` only at the terminal where you are configuring Cloudflare.
Do not copy the secret into tickets, screenshots, logs, or git.

It prints a short checklist only:

```text
Worker name/source/secret/vars
Exact forwards
Exact drops
Worker catch-all route
Health URL
Setup order
```

## Stage 3: Cloudflare Worker configuration

Cloudflare is currently manual/config-assisted.

Use the generated files instead of inventing one-off dashboard code:

```text
runtime/cloudflare-mail-worker/greatlibrary.io/cloudflare/worker-dashboard-paste.md
runtime/cloudflare-mail-worker/greatlibrary.io/cloudflare/wrangler-commands.md
runtime/cloudflare-mail-worker/greatlibrary.io/worker/src/index.ts
runtime/cloudflare-mail-worker/greatlibrary.io/worker/wrangler.toml
```

The Worker must have an `email()` handler and these bindings:

```text
MAIL_INGEST_SECRET = value from secrets/mail_ingest_secret
MAIL_INGEST_URL    = https://mail-ingest.greatlibrary.io/inbound/cloudflare-email
MAIL_INGEST_SECRET_HEADER = X-Main-Computer-Mail-Secret
```

The Worker receives the raw inbound message from Cloudflare Email Routing and
posts it to the Coolify ingest URL with:

```text
Content-Type: message/rfc822
X-Envelope-From: <sender>
X-Envelope-To: <recipient>
X-Main-Computer-Mail-Secret: <shared secret>
```

The Coolify ingest service writes the raw RFC822 body to the recipient's Maildir.

## Stage 4: Cloudflare Email Routing

Use the generated routing plan:

```text
runtime/cloudflare-mail-worker/greatlibrary.io/cloudflare/manual-routing-plan.md
```

The current intended routing is:

```text
johnrraymond@greatlibrary.io -> johnrraymondesq@gmail.com
info@greatlibrary.io         -> drop
*@greatlibrary.io            -> greatlibrary-mail-ingest Worker
```

In the Cloudflare dashboard, exact custom-address routes and the catch-all rule
are separate controls. Do not create a custom address named `*`; configure the
catch-all rule in the catch-all section.

Destination forwarding to Gmail requires the destination address to be verified.
If the Gmail destination is not verified yet, create/verify it first, then enable
the rule.

The catch-all should be enabled only after both of these are true:

```text
Coolify ingest app is running and healthy
Worker exists with the matching secret
```

## Verification

After the real Coolify apply, verify the public HTTP route:

```powershell
curl.exe https://mail-ingest.greatlibrary.io/healthz
```

Expected response:

```json
{"ok": true, "service": "mail-ingest"}
```

Then verify routing by sending test messages:

```text
johnrraymond@greatlibrary.io -> should arrive at Gmail
info@greatlibrary.io         -> should not be delivered to the worker mailbox
test-mailbox@greatlibrary.io -> should be delivered through Worker -> ingest -> Maildir
```

The final Maildir path on the Coolify volume is shaped like:

```text
/var/mail/greatlibrary.io/test-mailbox/Maildir/new/<message-file>
```

## Troubleshooting

### The dry-run looks good but nothing appears in Coolify

`--dry-run` does not mutate Coolify. Run the same `coolify-apply` command without
`--dry-run`.

### The `mail` project is not found

At the current checkpoint, the Coolify apply stage expects the target project to
exist or be selected by UUID. Create/select the project in Coolify, or pass a
known `--coolify-project-uuid`.

A future patch can add scripted project creation.

### The Worker does not appear in the Email Routing destination list

Cloudflare only lists Workers that exist and have an email handler. Deploy or
create the generated Worker first, then refresh the Email Routing page.

### The catch-all sent a message to the wrong place

Make sure exact routes exist before the catch-all:

```text
johnrraymond -> Gmail
info         -> Drop
catch-all    -> Worker
```

Exact address rules should take precedence over the catch-all. Keep the routing
plan in the generated `cloudflare/manual-routing-plan.md` as the operator source
for the dashboard configuration.

### The ingest app returns 403

The Worker and Coolify app do not agree on the shared secret or header name.
Compare:

```text
secrets/mail_ingest_secret
Worker secret: MAIL_INGEST_SECRET
Coolify env:   MAIL_INGEST_SECRET
header name:   X-Main-Computer-Mail-Secret
```

Do not rotate the secret unless both sides are redeployed.

### The ingest app returns 400 for a recipient

The ingest service only accepts recipients for the configured domain and safe
local parts. Confirm the Worker is passing the original recipient in:

```text
X-Envelope-To
```

## Operational rules

Keep these invariants true:

```text
The contract is the handoff between stages.
The secret file is the shared authentication material.
The Coolify stage consumes the contract; it should not be retyped manually.
Cloudflare dashboard work should follow generated instructions.
Do not enable catch-all until Worker and ingest are ready.
Do not expose SMTP/25 for this hidden-origin flow.
```

## Future work

Planned next increments:

```text
Add a contract-based verify action.
Add scripted Coolify project creation if the project is missing.
Add the mail user registry foundation.
Add password hashing and admin password reset commands.
Make ingest deliver only to known users/aliases, with quarantine for unknowns.
Add a webmail/mailbox reader profile.
Add outbound relay configuration after abuse controls exist.
Optionally add Cloudflare API apply as a separate, explicit stage.
```

