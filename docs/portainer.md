# Portainer runbook

Everything about how this service is deployed. Portainer is the only thing that
runs containers here; the repository is the source of truth.

## The stack

| Thing | Value |
|---|---|
| Portainer | `https://100.115.65.82:9443` (CE 2.45, endpoint id **3**) |
| Stack | name `delivery-calendar`, **id 14**, type Compose standalone |
| Git source | id **3**, `https://github.com/ShalevS343/delivery-calendar` |
| Reference | `refs/heads/main` |
| Compose path | `docker-compose.yml` |
| Image | `ghcr.io/shalevs343/delivery-calendar:latest` (public) |
| Container | `delivery-calendar` (name is fixed by compose) |
| Data | `/mnt/ssd/data/apps/delivery-calendar` → `/data`, `/secrets`, `/logs` |
| API token | `/home/server/.portainer-token` (mode 600) |

## How a change reaches the Pi

```
git push main
   → GitHub Actions "image" builds linux/arm64 and pushes GHCR
   → Portainer source poll (every 5 min) pulls the latest commit and image
   → docker compose recreates the container if the digest changed
```

Measured on 2026-11-26: commit `a020ab3` pushed at 19:57 was live at 20:00:37,
i.e. one poll tick after the image was available. The container restarted on the
new digest and was healthy within ~40 s.

## Trigger a redeploy now (don't wait for the poll)

```bash
curl -sk -X PUT \
  -H "X-API-Key: $(cat /home/server/.portainer-token)" \
  -H 'Content-Type: application/json' -d '{}' \
  "https://localhost:9443/api/stacks/14/git/redeploy?endpointId=3"
```

This pulls the newest commit from GitHub *and* the newest image, then rebuilds
the compose project. Equivalent to **Pull and redeploy** in the UI. Responds in
a few seconds; the container restart happens right after (watch `docker ps`).

## Read the current state

```bash
TOKEN=$(cat /home/server/.portainer-token)
curl -sk -H "X-API-Key: $TOKEN" https://localhost:9443/api/stacks/14 |
  python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["GitConfig"]["ConfigHash"], s.get("UpdateDate"))'
```

`ConfigHash` is the commit Portainer last deployed. `UpdateDate` only moves when
a poll tick runs, so it is not proof of a deploy.

## Change configuration

Environment variables live on the stack, never in git. To change one, post the
full git payload for the stack (this is the route Portainer 2.45 uses for git
stacks — `PUT /api/stacks/14` with an empty body returns 400):

```bash
curl -sk -X POST \
  -H "X-API-Key: $(cat /home/server/.portainer-token)" \
  -H 'Content-Type: application/json' \
  "https://localhost:9443/api/stacks/14/git?endpointId=3" \
  -d '{
        "AutoUpdate": {"Interval": "5m", "Webhook": ""},
        "ConfigFilePath": "docker-compose.yml",
        "RepositoryReferenceName": "refs/heads/main",
        "SourceID": 3,
        "Prune": false,
        "Env": [
          {"name": "MAIL_USER", "value": "you@gmail.com"},
          {"name": "MAIL_APP_PASSWORD", "value": "…"},
          {"name": "CALENDAR_ID", "value": "…@group.calendar.google.com"},
          {"name": "UI_PASSWORD", "value": "…"},
          {"name": "DRY_RUN", "value": "1"}
        ]
      }'
```

Rules that keep this from biting:

- **Send the complete `Env` array.** It replaces the stored set; omitting a
  variable silently empties it.
- `DRY_RUN=1` is the safe default. Flip to `0` only after credentials are in.
- Secrets are only ever stack variables. The service-account JSON lives on the
  SSD at `/mnt/ssd/data/apps/delivery-calendar/secrets/` and is bind-mounted.

## Gotchas we hit (so you don't)

1. **Portainer's updater pulls images; it never builds them.** A compose file
   with `build: .` fails every poll with
   `Some service image(s) must be built from source`. That is why the image is
   built in GitHub Actions and `docker-compose.yml` has no `build:`.
2. **Polling is configured on the Git *source*, not on the stack.** Setting only
   the stack's `AutoUpdate` starts no job; setting only the source interval
   makes ticks run that deploy nothing. Both must be set:
   source `interval=5m` (`PUT /api/gitops/sources/3 {"interval":"5m"}`) **and**
   stack `AutoUpdate.Interval=5m` (the `/stacks/14/git` payload above).
3. **`git/redeploy` is `PUT`, not `POST`.** `POST` answers 405, which reads like
   a missing route.
4. **The package must stay public** for the Pi to pull without credentials.
   If it is ever switched to private, add a GHCR registry entry in Portainer
   (Registries → Add registry → GitHub Container Registry) and select it on the
   stack.
5. **Never run `docker compose up` in the working clone.** The container name is
   fixed, so a second project collides and Portainer loses track of what it owns.

## Rollback

Every build is tagged with its commit:

```
ghcr.io/shalevs343/delivery-calendar:<sha>
```

Set `IMAGE=ghcr.io/shalevs343/delivery-calendar:<sha>` in the stack's environment
and redeploy. Revert the Git commit too, so the next poll does not put the new
code back.

## Teardown

```bash
curl -sk -X DELETE -H "X-API-Key: $(cat /home/server/.portainer-token)" \
  "https://localhost:9443/api/stacks/14?endpointId=3"
```

The container and network go; `/mnt/ssd/data/apps/delivery-calendar` (SQLite,
raw mail, rules, secrets) stays. The GHCR package and the Gmail app password are
separate and must be revoked on their own.
