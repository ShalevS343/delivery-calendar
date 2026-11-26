# Operations

## Stack

- Source of truth: <https://github.com/ShalevS343/delivery-calendar>
- Deployment: Portainer stack `delivery-sync`, built from that repository
  (commit on `main` or an explicit "Pull and redeploy" in Portainer)
- Image: `delivery-sync:local` (built by Portainer on the Pi)
- Container: `delivery-sync`
- State: `/mnt/ssd/data/apps/delivery-sync/` (SQLite, raw mail, rules, secrets)
- Logs: `/mnt/ssd/data/apps/delivery-sync/logs/` and `docker logs delivery-sync`
- UI: `http://100.115.65.82:8099` (basic auth; password is a stack variable)

The container runs as uid 1000 with a read-only root filesystem, `cap_drop: ALL`,
`no-new-privileges`, and one writable volume. The scheduler runs inside the
container every `POLL_INTERVAL_MINUTES` (60), with a run at startup.

Nothing is deployed from `/home/server/projects/delivery-sync` any more — that
directory is a working clone. `docker compose up` there would create a second
project with a container-name conflict; use Portainer.

## Health

```bash
docker ps --filter name=delivery-sync
curl -s http://127.0.0.1:8099/healthz
docker logs --tail 50 delivery-sync
```

`/healthz` is unauthenticated (the container healthcheck uses it) and returns:

- `ok` — false when the last run failed or is older than `2 × interval + 60 min`
- `last_run_status` — `ok`, `error`, or `idle` (no mailbox configured yet)
- `pending_review`, `dry_run`

A healthy container logs one `run finished` line per hour.

## What lives where

| Path | Contents |
|---|---|
| `/data/delivery.db` | SQLite: messages, shipments, updates, settings |
| `/data/raw/YYYY/MM/*.eml` | raw mail, 30-day retention |
| `/data/rules/*.yaml` | live rules, seeded from the image on first boot |
| `/data/heartbeat` | touched per run |
| `/data/secrets/` | service-account key or OAuth token, mode 0600 |
| `/data/logs/` | log directory for the container |

## Rules

Rules are data and can be edited without a rebuild:

```bash
sudo -u server $EDITOR /mnt/ssd/data/apps/delivery-sync/rules/promos.yaml
docker exec delivery-sync delivery-sync reload
```

An invalid file is rejected with the file name and line; the previous rules stay
active. `delivery-sync reload` prints the loaded families so you can see what is
in effect.

## Day-to-day actions in the UI

- **Accept** — a human confirmed the parsed record; review flag clears and the
  record's source becomes `manual`.
- **Suppress** — the shipment is wrong or unwanted: its event is deleted and will
  never be recreated. Deleting an event in Google Calendar is *not* permanent;
  reconcile recreates it.
- **Resync calendar** — force one shipment through create/patch.
- **Skipped** — mail the prefilter rejected, with the rule that rejected it.
  This is how a false negative stays recoverable.

## Re-running and re-parsing

```bash
docker exec delivery-sync delivery-sync run          # poll now
docker exec delivery-sync delivery-sync events       # arrival payloads only
docker exec delivery-sync delivery-sync reparse --limit 50
```

Only arrival statuses (`ready_for_pickup`, `delivered`) ever produce calendar
calls, so `events` printing nothing while shipments exist is normal and means
"nothing has arrived yet".

## Backup

SQLite in WAL mode; a consistent copy needs `.backup`, not `cp`:

```bash
sqlite3 /mnt/ssd/data/apps/delivery-sync/delivery.db \
  ".backup '/mnt/ssd/backups/delivery-sync/delivery-$(date +%F).db'"
```

Suggested nightly cron line:

```
15 3 * * * sqlite3 /mnt/ssd/data/apps/delivery-sync/delivery.db ".backup '/mnt/ssd/backups/delivery-sync/delivery-$(date +\%F).db'" && find /mnt/ssd/backups/delivery-sync -name 'delivery-*.db' -mtime +14 -delete
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `idle` runs | `MAIL_USER` / `MAIL_APP_PASSWORD` empty in `.env` |
| `fetch: ...` error | app password revoked; IMAP login blocked; folder name wrong (`[Gmail]/Deliveries` must exist) |
| `GOOGLE_SA_FILE not found` | key not at `/mnt/ssd/data/apps/delivery-sync/secrets/service-account.json` |
| `403` from Calendar | calendar not shared with the service account, or shared read-only |
| Event missing though the mail arrived | open the record in the UI: `needs_review`, `suppressed`, or status not yet arrived |
| Duplicate events | should be impossible via `iCalUID`; check `calendar_uid` is unchanged in the DB, and never mint a second one |
| Parse regression after a rules edit | `delivery-sync reload` shows the error and keeps the old rules |

## Upgrade

Push to `main` (or use Portainer's editor for a one-off patch on the stack):

```bash
git push origin main
```

Then either wait for the stack's polling interval or hit **Pull and redeploy** in
Portainer. The image is rebuilt from the new commit; the data volume and the
secrets file are untouched.

The SQLite schema is created with `CREATE TABLE IF NOT EXISTS`; a future column
change needs a migration step in `Store.migrate()`.
