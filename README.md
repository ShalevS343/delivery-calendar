# delivery-calendar

Reads delivery email from a personal Gmail (IMAP + app password), normalizes it
into one strict SQLite record per **shipment**, and writes **one all-day Google
Calendar event when the parcel reaches its delivery point** — pickup point or
door. Nothing else from the mail stream reaches the calendar: no "shipped", no
"in transit", no "out for delivery", no ETA churn.

- Design: `/home/server/docs/superpowers/specs/2026-11-25-delivery-mail-calendar-design.md`
- Build plan: `/home/server/docs/superpowers/plans/2026-11-25-delivery-mail-calendar.md`
- Setup: `docs/setup.md` · Operations: `docs/operations.md`

## What a record carries

Tracking number, carrier, merchant, order number, item summary, ETA (stored, not
shown in the calendar), arrival date, pickup deadline, the destination address
and its kind (`destination` / `pickup_point` / `depot`), a fallback address, the
"confirm receipt / approve / reschedule" link when the mail has one, and a
confidence plus the reasons a record needs a human.

## Deployment

Deployed by **Portainer from Git**, not from this directory:

- Repository: <https://github.com/ShalevS343/delivery-calendar>
- Portainer stack: `delivery-calendar` (Build method: Repository, path
  `docker-compose.yml`, ref `refs/heads/main`)
- Configuration: Portainer stack environment variables (`MAIL_USER`,
  `MAIL_APP_PASSWORD`, `CALENDAR_ID`, `UI_PASSWORD`, `DRY_RUN`, …). Nothing
  secret is committed; `.env.example` documents every variable and its default.
- Secrets on disk: `/mnt/ssd/data/apps/delivery-calendar/secrets/service-account.json`

A push to `main` plus the stack's polling (or **Pull and redeploy**) is the
deployment. See `docs/setup.md` and `docs/operations.md`.

## Quick start (dry run, no credentials)

```bash
cp .env.example .env
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/delivery-calendar check
.venv/bin/pytest
```

## Running against the fixtures

```bash
.venv/bin/pytest tests/test_extract.py -v
```

## Layout

```
delivery_calendar/   code (config, schema, extraction, store, calendar, UI)
rules/           seed YAML: tracking families, carriers, promos, addresses
schema/          generated JSON Schema for the shipment record
tests/           unit + fixture tests, fake IMAP and fake Calendar
```

## Design rules worth knowing before changing code

- **The calendar is arrival-only.** `sync_shipment` returns without touching the
  API for any non-arrival status. Do not add ETA events "just this once".
- **Identity is `calendar_uid`, never `shipment_key`.** Keys change when a
  tracking number appears; the UID and therefore the Google event must not.
- **A checksum failure is a review flag, not a deletion.** Dropping a real
  tracking number loses a delivery.
- **A missing address is better than a wrong one.**
- **No model in the default path.** Small local LLMs invent digits; the model
  tier is off and post-v1.
