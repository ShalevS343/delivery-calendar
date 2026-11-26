# Setup

Everything here is owner-run, once. The service refuses to touch the calendar
until the mailbox and the calendar share are in place.

## 1. Gmail app password (mail read over IMAP)

1. Google Account → Security → turn on **2-Step Verification** (required).
2. Go to <https://myaccount.google.com/apppasswords>, create an app password
   named `delivery-calendar`.
3. Put the 16 characters (spaces are fine) in `/home/server/projects/delivery-calendar/.env`:

   ```
   MAIL_USER=you@gmail.com
   MAIL_APP_PASSWORD=abcd efgh ijkl mnop
   ```

Optional but recommended for precision: in Gmail, create a filter for courier
and shop senders that applies the label `Deliveries`; then set
`MAIL_FOLDER="[Gmail]/Deliveries"`. The promo prefilter is the safety net either
way.

## 2. Calendar

1. Google Calendar → **Create new calendar** → name it `Deliveries`.
2. Settings → copy the **Calendar ID** (looks like an email address, ends in
   `@group.calendar.google.com`).
3. Put it in `.env` as `CALENDAR_ID=...`.

The calendar stays owned by you. The service only writes to it.

## 3. Google Cloud service account (default auth)

1. <https://console.cloud.google.com> → new project `delivery-calendar`.
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **IAM & Admin → Service Accounts** → create `delivery-calendar` → done.
4. Open it → **Keys** → Add key → JSON → download.
5. Save it as:

   ```
   /mnt/ssd/data/apps/delivery-calendar/secrets/service-account.json
   chmod 600 that file
   ```

6. Copy the service account email
   (`delivery-calendar@<project>.iam.gserviceaccount.com`).
7. Back in Google Calendar → `Deliveries` → **Settings and sharing** → **Share
   with specific people** → paste the service account email → permission
   **Make changes to events** → Send.

No consent screen, no verification, no token expiry. If Google blocks the share:

## 3b. OAuth fallback

Set `CALENDAR_AUTH=oauth`, create an OAuth client of type **Desktop app**,
publish the consent screen (unverified is fine: one warning screen, 100-user
cap), authorize once on a machine with a browser, copy the resulting
`token.json` to `secrets/token.json`. This path relies on unverified-app
refresh-token behaviour, which is why it is not the default.

## 4. Deploy from Git through Portainer

The repository is <https://github.com/ShalevS343/delivery-calendar> (public, no
secrets). Portainer owns the deployment; the Pi is not a place you edit compose
files on.

1. Copy `schema/`, `rules/` and everything else you need — nothing to copy. The
   repo is self-contained; only the secrets file lives on the Pi:
   `/mnt/ssd/data/apps/delivery-calendar/secrets/service-account.json`.
2. Portainer → **Stacks → Add stack**.
3. Name: `delivery-calendar`.
4. Build method: **Repository**.
   - Repository URL: `https://github.com/ShalevS343/delivery-calendar`
   - Compose path: `docker-compose.yml`
   - Repository reference: `refs/heads/main`
5. **Environment variables** (Portainer stores these server-side; they are never
   committed):

   ```
   MAIL_USER=you@gmail.com
   MAIL_APP_PASSWORD=abcd efgh ijkl mnop
   CALENDAR_ID=...@group.calendar.google.com
   UI_PASSWORD=<the password you want for the UI>
   DRY_RUN=1
   ```

   Everything else has a default in `docker-compose.yml`.
6. **Deploy the stack.** Portainer clones the repo, builds `delivery-calendar:local`
   on the Pi and starts it.
7. Turn on **GitOps updates** for the stack (Stack details → Automatic updates →
   Polling, 5 minutes, or copy the webhook into GitHub). From then on a push to
   `main` is the deployment.

`DRY_RUN=1` on first deploy is intentional: it fetches, parses and stores, and
prints the event it *would* create, but never calls the calendar.

> The repo is public. If you'd rather it was private, flip it in GitHub settings
> and add a read-only credential to the Portainer stack — Portainer then clones
> with that credential.

## 5. Verify, then go live

```bash
# health and what the last run did
curl -s http://127.0.0.1:8099/healthz

# what would have been written to the calendar
docker exec delivery-calendar delivery-calendar events | head -60

# real mail headers the service can see
docker exec delivery-calendar delivery-calendar fetch --limit 5
```

Open the review UI at `http://100.115.65.82:8099` (user `admin`, password from
the stack's `UI_PASSWORD`). Anything the extractor was unsure about is waiting
there.

When the dry-run output looks right, change `DRY_RUN=0` in the stack's
environment variables and hit **Update the stack**. Then send yourself a real
arrival mail (or wait for one), confirm one all-day event appears on the
`Deliveries` calendar on the arrival date, and that the iPhone shows it with a
tappable location.

## 6. Fixtures (optional, for tuning the parser)

```bash
# real mail, with addresses: keep on the SSD, never in git
docker exec delivery-calendar delivery-calendar export-fixtures --limit 30 \
  --out /data/fixtures

# copy them out to the test suite when you want to iterate
docker cp delivery-calendar:/data/fixtures ./fixtures
```

`delivery-calendar reparse --limit 50` re-runs extraction over stored mail after a
rules change, without waiting for new mail.
