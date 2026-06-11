# Main Computer local website platform

This Docker Compose platform is the direct-port foundation for the future
Applications → Website Builder workflow.

It intentionally does **not** use WSL, SSH, Coolify, Caddy, Traefik,
hostnames, HTTPS, port 80, or port 443. A reverse proxy can be added later
after the Docker-shaped local workflow is stable.

## Direct local URLs

- Hub Site local publish: <http://0.0.0.0:18080/>
- Blog Site local publish: <http://0.0.0.0:18081/>
- Hub Site dev publish: <http://0.0.0.0:18082/>
- Blog Site dev publish: <http://0.0.0.0:18083/>

## Commands

Start all local-platform services:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\up-local-platform.ps1
```

Publish/restart one registered website lane:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\publish-website.ps1 -SiteId hub-site -Lane dev
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\publish-website.ps1 -SiteId hub-site -Lane local
```

The `local` lane is the current local/prod-style direct-port lane. The Python
publish tool also accepts `prod` or `production` as aliases for `local`.


Manage one website Docker lane with the registry-backed lifecycle command:

```powershell
python .\tools\local-platform\website-docker.py install hub-site
python .\tools\local-platform\website-docker.py start hub-site --lane dev
python .\tools\local-platform\website-docker.py stop hub-site --lane dev
python .\tools\local-platform\website-docker.py publish hub-site --lane dev
python .\tools\local-platform\website-docker.py verify hub-site --lane dev
python .\tools\local-platform\website-docker.py logs hub-site --lane dev
```

By default, the lifecycle command uses a site-local Compose file:

```text
runtime/local-platform/sites.json
runtime/websites/<site-id>/.main-computer/local-platform/docker-compose.yml
```

That keeps each website under its own Docker Compose project, such as
`main-computer-website-hub-site`, instead of requiring the legacy all-websites
Compose file to be edited or regenerated for normal site work.

The legacy aggregate file remains available for compatibility and migration:

```powershell
python .\tools\local-platform\website-docker.py start hub-site --lane dev --compose-scope aggregate
python .\tools\local-platform\website-docker.py stop hub-site --lane dev --compose-scope aggregate
```

The older `publish-website.py` and `publish-website.ps1` commands remain
available for compatibility while the Website Builder is being wired to the
new lifecycle command.

Verify endpoints:

```powershell
python .\tools\local-platform\verify-local-platform.py
```

Stop the platform:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\down-local-platform.ps1
```

Generate selected site-local Compose files:

```powershell
python .\tools\local-platform\generate-websites-compose.py --repo-root . --site hub-site --site johnrraymond
```

Generated site files are written to:

```text
runtime/websites/<site-id>/.main-computer/local-platform/docker-compose.yml
```

These site-local Compose files are generated local runtime artifacts. They are
recreated by the lifecycle/generation commands and should not be committed in
place of the website source/config.

Generate the legacy aggregate all-websites Compose file only when explicitly
needed for migration or debugging:

```powershell
python .\tools\local-platform\generate-websites-compose.py --repo-root .
```

The aggregate generated file is written to:

```text
deploy/local-platform/generated/docker-compose.websites.yml
```

The aggregate file is also generated local runtime output. Normal website
lifecycle commands no longer need it because they default to site-scoped Compose.

The site server reads files from `runtime/websites/<site-id>/`, so edits saved
by the Website Builder are visible to the local Docker service after publish.
Responses are sent with `Cache-Control: no-store` so the embedded preview updates
after a save/publish loop instead of showing stale CSS or HTML.
