# Website Builder remote Coolify publishing runbook

Status: working operator draft, first captured 2026-06-07.

This document records the remote Coolify deployment path for Website Builder
publishing. It is intentionally written as an operator runbook so the deployment
shape, Main Computer configuration, and missing product work stay visible as the
remote host is brought online.

## Goal

Make Website Builder publish a saved site to a remote host through a Coolify
managed server, then verify that the public URL serves the published site.

The first target is a single remote Linux VPS:

```text
operator workstation
  -> Main Computer Website Builder
  -> remote Coolify instance
  -> same remote server's Docker/Traefik destination
  -> public HTTPS website domain
```

Later targets can split management and workload servers, but the first pass
should keep Coolify and the website runtime on the same box so DNS, TLS, Docker,
and publish verification are easier to reason about.


For Coolify on crypto-network/testnet machines, use the separate runbook:

```text
pretty_docs/crypto-network-coolify-testnet-runbook.md
```

That path has additional safety checks for Besu/QBFT ports, RPC exposure, and
validator/data separation. The Website Builder publishing path should not be used
as the crypto-network deployment contract.


## Docker-first install rule

Do not treat the Coolify quick installer as the first verification step on a
remote host. Before installing Coolify, install or verify a working Docker stack
and prove that the Docker daemon API responds quickly:

```bash
docker version
docker compose version
timeout 5 curl --unix-socket /var/run/docker.sock http://localhost/_ping
timeout 5 docker ps
timeout 5 docker info >/tmp/docker-info.txt && echo "docker info ok"
```

Expected socket result:

```text
OK
```

If Docker commands or raw socket `/_ping` hang, stop and fix Docker before
installing Coolify. For crypto-network/testnet hosts, follow the pinned Docker
path in:

```text
pretty_docs/crypto-network-coolify-testnet-runbook.md
```

## Current repository state

The current snapshot already has part of this flow:

- Website Builder has a `Publish` button and `remote_prod` publish target shape.
- `main_computer/website_project_manifest.py` records remote target fields such
  as `controller_id`, `domain`, `publish_mode`, `site_slug`, `source_path`,
  `remote_host`, and `remote_root`.
- `main_computer/deployment_controllers.py` stores Coolify deployment controller
  registrations under runtime state.
- `tests/test_website_publish_targets.py` verifies that remote publish target
  configuration is saved.
- `tools/local-platform/diagnose-remote-publish-404.py` diagnoses Coolify API
  token, UUID, resource, and `/deploy` failures.
- `tools/local-prod/README-coolify-local-docker.md` documents the local Coolify
  smoke path.

The snapshot does **not** yet contain the command scripts referenced by the
remote publish plan:

```text
deploy/coolify/push_site_scp.py
deploy/coolify/push_site_local.py
```

Until those scripts, or an equivalent Coolify API deploy path, are added, this
runbook is the remote host setup contract and the Website Builder publish path is
not end-to-end complete.

## Phase 0 - choose the first remote shape

Use a single VPS for the first rehearsal.

Suggested rehearsal host:

```text
Ubuntu LTS
4 vCPU
8 GB RAM
80 GB+ disk
root SSH access from the operator IP
```

Coolify's current minimum is lower, but Website Builder rehearsals may include
builds, static site serving, API/runtime tests, and later CMS/database services.
The larger rehearsal box avoids false negatives caused by memory pressure.

Pick names before install:

```text
Coolify dashboard: https://coolify.example.com
Website wildcard:  https://*.sites.example.com
First site:         https://johnrraymond.sites.example.com
```

The dashboard domain and site wildcard may share the same VPS IP. Avoid using the
same exact hostname for both the dashboard and a site.

## Phase 1 - create DNS and firewall before install

Create DNS records pointing to the VPS public IP:

```text
A  coolify.example.com       -> <VPS_IP>
A  *.sites.example.com       -> <VPS_IP>
```

For a simpler early smoke, a single site record is also fine:

```text
A  johnrraymond.example.com  -> <VPS_IP>
```

Open these inbound ports at the cloud firewall:

```text
22/tcp    from operator IP only
80/tcp    public
443/tcp   public
8000/tcp  operator IP only during initial dashboard setup
6001/tcp  operator IP only during initial setup if direct dashboard access needs it
6002/tcp  operator IP only during initial setup if terminal access needs it
```

After the dashboard is reachable through its HTTPS domain, prefer closing direct
public access to `8000`, `6001`, and `6002` and keeping only `22`, `80`, and
`443` open.

## Phase 2 - install Coolify on the remote VPS

SSH to the server as root:

```bash
ssh root@<VPS_IP>
```

Check the base system:

```bash
cat /etc/os-release
curl --version
```

Run the official quick installer. This is the documented Coolify install
command and should be the default form used in setup notes and handoffs:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

If you are already in a root shell on a minimal host where `sudo` is
unavailable, run the same installer under root:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

After installation, check the containers:

```bash
docker ps -a --filter "name=coolify"
docker compose version
```

Open the dashboard at the temporary direct URL if needed:

```text
http://<VPS_IP>:8000
```

Create the owner account, then configure the Coolify instance URL to the
dashboard hostname:

```text
https://coolify.example.com
```

## Phase 3 - configure the Coolify server for website resources

In Coolify:

1. Open the server where resources will run.
2. Confirm Docker is reachable and the proxy is enabled.
3. Keep Traefik as the default proxy for the first pass.
4. Set the server wildcard domain to the website base, for example:

```text
https://sites.example.com
```

5. Verify that a new resource can receive a generated subdomain under the
   wildcard.
6. Create or deploy a tiny static/container smoke resource and assign it a test
   domain such as:

```text
https://smoke.sites.example.com
```

The smoke passes only when the public HTTPS URL returns the expected content and
the browser does not show a certificate warning.

## Phase 4 - enable API access for Main Computer

Coolify API access is needed for diagnostics now and for the future deploy API
path.

In Coolify:

1. Go to Settings -> Advanced.
2. Enable API access.
3. Optionally restrict allowed IPs to the operator or Main Computer host.
4. Go to Security -> API Tokens.
5. Create a token for the active team that owns the target server/resources.
6. Prefer least privilege:
   - `read` for diagnostics and discovery.
   - `read` + `deploy` for deploy triggering.
   - Add `write` only for automation that creates or mutates resources.
   - Avoid `root` for routine Website Builder publishing.

Store the token outside Git. A local PowerShell session can use:

```powershell
setx MAIN_COMPUTER_COOLIFY_REMOTE_TOKEN "<token>"
```

For the current process only:

```powershell
$env:MAIN_COMPUTER_COOLIFY_REMOTE_TOKEN = "<token>"
```

## Phase 5 - register the remote Coolify controller in Main Computer

Deployment controller registry state lives under:

```text
runtime/deployment/controllers.json
```

It is runtime state and should not be committed with secrets.

The controller shape should look like this:

```json
{
  "id": "coolify-remote-prod",
  "kind": "coolify",
  "name": "Remote Production Coolify",
  "base_url": "https://coolify.example.com",
  "token_ref": "MAIN_COMPUTER_COOLIFY_REMOTE_TOKEN",
  "roles": ["remote-prod"],
  "default_for": ["remote-prod"],
  "local": false
}
```

Verification from the operator shell:

```powershell
python .\tools\local-platform\diagnose-remote-publish-404.py hub-site --lane remote-prod --skip-docker-route
```

Expected early result before a real resource is selected:

```text
Coolify API auth works, but no deployable resource UUID is stored for the site.
```

That is acceptable during setup. A `401`, `403`, unreachable base URL, or empty
team/resource view means the controller/token/team is not ready.

## Phase 6 - configure the Website Builder site target

For the first end-to-end path, keep the source path as the saved Website Builder
site directory:

```text
runtime/websites/<site-id>
```

Recommended first remote target values:

```text
lane:         remote_prod
publish_mode: scp
site_slug:    <public-site-slug>
source_path:  runtime/websites/<site-id>
remote_host:  root@<VPS_IP>
remote_root:  /srv/main-computer/sites
domain:       https://<public-site-slug>.sites.example.com
controller_id: coolify-remote-prod
environment:  production
```

`publish_mode=scp` means Main Computer will push the built static/runtime bundle
to the remote host. `publish_mode=local_server` is a local Coolify rehearsal
shortcut and should not be used as the real remote production mode.

Current blocker: the repo snapshot does not yet include
`deploy/coolify/push_site_scp.py`, so the remote publish plan will report the
missing command script until a follow-up patch adds it.

## Phase 7 - target remote file/runtime layout

Use one durable root for Main Computer-published static sites:

```text
/srv/main-computer/sites/<site_slug>/
```

Expected files after a static site publish:

```text
/srv/main-computer/sites/<site_slug>/site.json
/srv/main-computer/sites/<site_slug>/index.html
/srv/main-computer/sites/<site_slug>/style.css
/srv/main-computer/sites/<site_slug>/script.js
/srv/main-computer/sites/<site_slug>/builder.json
/srv/main-computer/sites/<site_slug>/runtime.js
/srv/main-computer/sites/<site_slug>/.main-computer/runtime/app.py
/srv/main-computer/sites/<site_slug>/.main-computer/runtime/runtime.json
```

A static-only site can be served by nginx or Caddy. A site requiring the bundled
Python site runtime should use the generated runtime app and a Coolify service
that routes the public domain to that app.

## Phase 8 - publish verification contract

A Website Builder remote publish is not considered successful just because a
copy command exits with code `0`.

Minimum verification:

```text
1. Coolify controller base URL is reachable.
2. API token can list/read resources for the expected team.
3. The target public domain resolves to the server IP.
4. HTTPS certificate is valid.
5. Public URL returns the saved page content.
6. Response is not a Coolify/Traefik 404 fallback.
7. If the site has a blog/CMS dependency, public /blog routes read only
   published content and draft content is not exposed.
```

Suggested operator probes:

```powershell
python .\tools\local-platform\diagnose-remote-publish-404.py hub-site --lane remote-prod --skip-docker-route
python - <<'PY'
import urllib.request
url = "https://johnrraymond.sites.example.com"
print(urllib.request.urlopen(url, timeout=20).status)
PY
```

## Phase 9 - known failure modes

### Dashboard works but site domain 404s

Likely causes:

```text
DNS points at the Coolify management host instead of the server running the resource.
No Coolify resource is attached to the requested domain.
Traefik has not picked up labels for the resource.
The resource is unhealthy, so path routing falls back to another app.
```

### API token works in browser but not from Main Computer

Likely causes:

```text
API access is disabled.
Allowed IPs excludes the Main Computer host.
Token was created under a different Coolify team.
Token lacks read/deploy permissions.
The token was not copied completely; Coolify only displays it once.
```

### Publish button is enabled but deploy fails with missing UUID

Current repo behavior is command-template based and does not use the old deploy
API path for remote publish. If a diagnostic references a missing deployable
resource UUID, capture it as evidence for the follow-up Coolify resource
selection patch rather than guessing `uuid=<site-id>`.

### Publish plan says command script is missing

This is expected in the current snapshot because these referenced files are not
present yet:

```text
deploy/coolify/push_site_scp.py
deploy/coolify/push_site_local.py
```

The next implementation patch should add at least the SCP publisher or replace
the command-template path with a Coolify API resource deployment path.

## Phase 10 - next documentation/code updates

Track updates in small patches:

1. Add this runbook.
2. Add the remote SCP publisher script and tests.
3. Add the Website Builder UI/operator path for accepting remote target values.
4. Add a Coolify resource discovery/persist step so Publish never guesses UUIDs.
5. Add remote publish smoke verification.
6. Add a remote Directus/blog dependency contract.

## External references checked

These URLs were checked while writing the runbook. Re-check them before changing
installation commands or required ports:

```text
https://coolify.io/docs/get-started/installation
https://coolify.io/docs/knowledge-base/server/firewall
https://coolify.io/docs/knowledge-base/server/introduction
https://coolify.io/docs/knowledge-base/dns-configuration
https://coolify.io/docs/knowledge-base/domains
https://coolify.io/docs/api-reference/authorization
https://coolify.io/docs/api-reference/api/operations/deploy-by-tag-or-uuid
```
