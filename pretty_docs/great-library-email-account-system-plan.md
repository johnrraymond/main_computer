# Great Library email account system plan

Status: product/engineering plan for turning the working hidden-ingest mail path
into Great Library hosted email accounts.

## Current checkpoint

Working now:

```text
Cloudflare Email Routing
  -> Cloudflare Email Worker
  -> https://mail-ingest.greatlibrary.io/inbound/cloudflare-email
  -> Coolify mail-ingest service
  -> Maildir storage
```

Confirmed behavior:

```text
Coolify ingest is deployed and healthy.
Authenticated HTTPS ingest returns 201.
Catch-all mail can reach the Worker and ingest service.
Mail is written under /var/mail/greatlibrary.io/<local>/Maildir.
```

This proves receiving and storing inbound mail. It does not yet provide user
accounts, passwords, signup, mailbox browsing, or outbound sending.

## Ultimate target

A person signs up through Great Library and receives a mailbox:

```text
Great Library signup
  -> user account
  -> alice@greatlibrary.io
  -> mailbox provisioned
  -> login at https://mail.greatlibrary.io
  -> user can read mail and change password
```

Cloudflare should not need one route per mailbox user. Keep Cloudflare simple:

```text
johnrraymond@greatlibrary.io -> external Gmail forward
info@greatlibrary.io         -> drop or reserved handling
catch-all                    -> Cloudflare Worker
```

The Great Library mail app decides which recipients are valid users or aliases.

## Core rule

Do not treat a random inbound recipient as a real account.

Current ingest behavior can create a Maildir for any safe local part. That was
useful for proving the pipeline. The account system should change delivery to:

```text
known user or alias -> deliver
unknown recipient   -> reject or quarantine
```

The user registry becomes the source of truth for receiving mail and logging in.


## Implementation checkpoint: registry foundation

Implemented foundation in `tools/cloudflare_mail_worker.py`:

```text
prepare writes mail-users.json next to mail-worker-contract.json without overwriting existing users.
user-add creates an enabled mailbox identity.
user-password-set stores a PBKDF2-SHA256 password hash only.
user-list prints users without exposing password hashes.
alias-add creates an alias that targets an existing user.
alias-list prints configured aliases.
```

The registry file is intentionally local to the generated contract directory for
this tranche:

```text
runtime/cloudflare-mail-worker/<domain>/mail-users.json
```

It is the future source of truth for both the ingest policy and the webmail login
layer. This patch does **not** yet change delivery policy; unknown catch-all
recipients can still be written by the deployed ingest app until the known-user
ingest tranche is applied.

## Feature set 1: user registry

Purpose: define who has a Great Library mailbox.

Minimum record:

```json
{
  "local": "alice",
  "address": "alice@greatlibrary.io",
  "display_name": "Alice",
  "enabled": true,
  "mail_enabled": true,
  "aliases": [],
  "created_at": "2026-06-15T00:00:00Z",
  "updated_at": "2026-06-15T00:00:00Z"
}
```

First implementation can use a durable JSON file on the mail volume. Move to
SQLite/Postgres when signup and the web app are active.

Needed CLI:

```powershell
python tools/cloudflare_mail_worker.py user-add `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --local alice `
  --display-name "Alice"

python tools/cloudflare_mail_worker.py user-list `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json

python tools/cloudflare_mail_worker.py user-disable `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --local alice
```

## Feature set 2: passwords and sessions

Purpose: let users log in and change passwords.

Password rules:

```text
Store password hashes only.
This foundation uses PBKDF2-SHA256 from the Python standard library as an
interim implementation. Prefer Argon2id when the dedicated webmail app dependency
set is introduced.
Never store plaintext passwords in contracts, generated docs, or Coolify output.
```

Initial admin commands:

```powershell
python tools/cloudflare_mail_worker.py user-password-set `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --local alice
```

Web app flow:

```text
Login
Settings -> Change password
Forgot password -> recovery email or admin reset
```

Change-password behavior:

```text
verify current password
validate new password
hash new password
replace stored hash atomically
revoke old sessions
write audit event
```

First public version can use admin resets only. Recovery email reset should wait
until signup verification is in place.

## Feature set 3: aliases and reserved names

Purpose: route multiple addresses to one mailbox and block sensitive names.

Example:

```text
alice@greatlibrary.io      -> alice mailbox
a.smith@greatlibrary.io    -> alice mailbox
alice.work@greatlibrary.io -> alice mailbox
```

Needed CLI:

```powershell
python tools/cloudflare_mail_worker.py alias-add `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --alias a.smith `
  --target alice

python tools/cloudflare_mail_worker.py alias-list `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json
```

Reserved names should not be self-service:

```text
admin
root
postmaster
abuse
security
info
support
billing
```

## Feature set 4: known-user ingest

Purpose: make inbound delivery respect the registry.

Ingest should load the registry and apply:

```text
recipient matches enabled user -> deliver
recipient matches alias        -> deliver to target user
recipient is disabled          -> reject or quarantine
recipient is unknown           -> quarantine first, reject later
```

Recommended first production policy:

```text
known users deliver
unknown recipients quarantine
```

This avoids silently losing mail while preventing random spam from creating real
mailboxes.

## Feature set 5: mailbox reader API

Purpose: expose Maildir messages to the email app.

Minimum endpoints:

```text
POST /login
POST /logout
GET  /api/messages
GET  /api/messages/<id>
POST /api/messages/<id>/read
POST /api/messages/<id>/unread
POST /api/messages/<id>/delete
POST /api/settings/password
```

The API maps the authenticated user to exactly one mailbox root:

```text
alice -> /var/mail/greatlibrary.io/alice/Maildir
```

Do not allow a user-controlled path to select mailboxes.

## Feature set 6: web email app

Purpose: user-facing mailbox experience.

Minimum screens:

```text
Login
Inbox
Message detail
Delete/archive
Settings
Change password
Logout
```

Expose through the web path:

```text
https://mail.greatlibrary.io
```

The webmail app should mount the same mail volume as the ingest service. The
first version can be read-mostly; sending mail is a separate feature set.

## Feature set 7: public signup

Purpose: let people create Great Library email accounts.

Signup flow:

```text
choose local part
enter password
enter recovery email
verify recovery email
create Great Library user
create mail user
provision Maildir
allow login
```

Rules:

```text
rate-limit signup
block reserved names
validate local part strictly
require recovery email verification
do not enable outbound sending by default
```

Signup should create accounts in the same registry used by ingest and webmail.

## Feature set 8: outbound sending

Purpose: allow users to send mail from Great Library.

Do not enable public-user outbound sending until abuse controls exist.

Recommended approach:

```text
use an authenticated relay or Cloudflare Email Sending
do not send direct SMTP from the VPS
start receive-only for new users
enable outbound per trusted user or after reputation controls
```

Needed controls:

```text
per-user rate limits
daily send limits
abuse disable switch
SPF/DKIM/DMARC alignment
sent folder
bounce handling
```

## Feature set 9: backups and migration

Purpose: protect accounts and mail.

Back up:

```text
mail-worker-contract.json
secrets/mail_ingest_secret
user registry
mail volume
Cloudflare Worker source/config notes
```

Needed tools:

```powershell
python tools/cloudflare_mail_worker.py backup-plan ...
python tools/cloudflare_mail_worker.py mailbox-export --local alice ...
python tools/cloudflare_mail_worker.py registry-export ...
```

## Feature set 10: observability and operator safety

Purpose: make failures obvious.

Needed:

```text
ingest logs for accepted/rejected/quarantined messages
Worker delivery failure logging
mailbox counts
quarantine counts
registry validation
smoke-test command
```

Suggested smoke test:

```powershell
python tools/cloudflare_mail_worker.py smoke-test `
  --contract runtime/cloudflare-mail-worker/greatlibrary.io/mail-worker-contract.json `
  --to alice@greatlibrary.io
```

## Recommended implementation order

Build in this order:

```text
1. User registry CLI and data file.
2. Password hash + admin password reset CLI.
3. Alias CLI and reserved-name enforcement.
4. Ingest registry enforcement with unknown-recipient quarantine.
5. Mailbox reader API.
6. Webmail login + inbox.
7. Change-password screen.
8. Public signup with recovery email verification.
9. Outbound sending for trusted users.
10. Backups, moderation, and admin UI polish.
```

## Next patch target

The next implementation patch should be:

```text
mail user registry foundation
```

It should add:

```text
mail-users.json data model
user-add
user-list
user-disable
user-password-set
alias-add
alias-list
tests
```

It should not yet enable public signup. Public signup should wait until the
registry, password hashing, and known-user ingest behavior are in place.
