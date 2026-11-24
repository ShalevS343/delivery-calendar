# Setup

Everything here is owner-run, once. The service refuses to touch the calendar
until the mailbox and the calendar share are in place.

## 1. Gmail app password (mail read over IMAP)

1. Google Account → Security → turn on **2-Step Verification** (required).
2. Go to <https://myaccount.google.com/apppasswords>, create an app password
   named `delivery-sync`.
3. Put the 16 characters (spaces are fine) in `/home/server/projects/delivery-sync/.env`:

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

1. <https://console.cloud.google.com> → new project `delivery-sync`.
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **IAM & Admin → Service Accounts** → create `delivery-sync` → done.
4. Open it → **Keys** → Add key → JSON → download.
5. Save it as:

   ```
   /mnt/ssd/data/apps/delivery-sync/secrets/service-account.json
   chmod 600 that file
   ```

6. Copy the service account email
   (`delivery-sync@<project>.iam.gserviceaccount.com`).
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

## 4. Deploy

```bash
cd /home/server/projects/delivery-sync
docker compose up -d --build
docker compose logs --tail 20
```

First boot with `DRY_RUN=1` is intentional: it fetches, parses and stores, and
prints the event it *would* create, but never calls the calendar.

## 5. Verify, then go live

```bash
# health and what the last run did
curl -s http://127.0.0.1:8099/healthz

# what would have been written to the calendar
docker exec delivery-sync delivery-sync events | head -60

# real mail headers the service can see
docker exec delivery-sync delivery-sync fetch --limit 5
```

Open the review UI at `http://100.115.65.82:8099` (user `admin`, password from
`.env`). Anything the extractor was unsure about is waiting there.

When the dry-run output looks right:

```bash
sed -i 's/^DRY_RUN=1/DRY_RUN=0/' .env
docker compose up -d
```

Then send yourself a real arrival mail (or wait for one), confirm one all-day
event appears on the `Deliveries` calendar on the arrival date, and that the
iPhone shows it with a tappable location.

## 6. Fixtures (optional, for tuning the parser)

```bash
# real mail, with addresses: keep on the SSD, never in git
docker exec delivery-sync delivery-sync export-fixtures --limit 30 \
  --out /data/fixtures

# copy them out to the test suite when you want to iterate
docker cp delivery-sync:/data/fixtures ./fixtures
```

`delivery-sync reparse --limit 50` re-runs extraction over stored mail after a
rules change, without waiting for new mail.
