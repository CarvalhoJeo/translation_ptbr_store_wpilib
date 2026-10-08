# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Plans 1 (earning points: accounts, ledger, Transifex sync) and 2 (catalog, redemptions, leaderboard, deploy) are implemented. Design: `docs/superpowers/specs/2026-10-07-translation-store-design.md`. Plans: `docs/superpowers/plans/`.

## Commands

- Install: `uv sync`
- Run tests: `uv run pytest` · one test: `uv run pytest tests/test_ledger.py::test_same_event_twice_is_ignored -v`
- Postgres-only concurrency tests: `docker run -d --rm --name store-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 postgres:17` then `DATABASE_URL=postgres://postgres:pg@localhost:55432/postgres uv run pytest -m postgres` (skipped on SQLite)
- Dev server: `uv run python manage.py migrate && uv run python manage.py runserver`
- Admin user: `uv run python manage.py createsuperuser`
- Track all frc-docs resources: `uv run python manage.py track_resources`
- Sync points manually / self-hosted cron: `flock -n /tmp/sync_transifex.lock sh -c 'uv run python manage.py track_resources && uv run python manage.py sync_transifex'` (exits non-zero if any resource failed). The lock prevents overlapping runs, and `track_resources` picks up newly added frc-docs pages. If a resource is deleted on Transifex, deactivate it in the admin.

## Deployment (Vercel, Hobby plan)

- Django is auto-detected (`manage.py` → `store.wsgi`). `[tool.vercel.scripts] build` runs `migrate` on every build; Vercel runs `collectstatic` itself.
- Postgres is Neon via the Vercel Marketplace (`DATABASE_URL` injected). SQLite does not persist on Vercel.
- Vercel Cron calls `GET /cron/sync/` daily (Hobby allows only daily crons; `vercel.json`). The view checks `Authorization: Bearer $CRON_SECRET`, runs `track_project_resources`, then `sync_all` with a `SYNC_TIME_BUDGET_SECONDS` (240 s) deadline under the 300 s function limit; resources not reached go first next run (ordered by `last_synced_at`).
- On Vercel (`VERCEL=1`) `.env` is ignored and a missing `DJANGO_SECRET_KEY` fails startup. `*.vercel.app` hosts come from `VERCEL_URL` / `VERCEL_PROJECT_PRODUCTION_URL` automatically.
- Also set `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (Gmail app password) and `BLOB_READ_WRITE_TOKEN` (from the Blob store). The daily cron also erases addresses delivered more than 30 days ago.
- Deploy: `vercel deploy --prod`. Production env vars: `vercel env ls production`.

## What this is

A reward store for the WPILib pt-BR translation team on Transifex. Translators earn points for words translated and reviewed in Brazilian Portuguese (Transifex language code `pt`, not `pt_BR`; project `o:wpilib:p:frc-docs`), only after `LAUNCH_AT`, and redeem them for physical swag. Admins approve account links and redemptions. Stack: Django + Postgres, server-rendered pt-BR UI, GitHub login (django-allauth).

## Architecture rules that span multiple apps

- **Progress comes from polling the free Transifex API** (`GET /resource_translations`), not Activity Reports, which are paid-only. `transifex.sync.sync_all` (called by the `/cron/sync/` view or the `sync_transifex` command) turns per-string changes into points.
- **`transifex.ProgressSource` is the boundary.** It yields normalized `ProgressEvent`s, and the ledger must never handle raw Transifex JSON.
- **The ledger is append-only.** Never update or delete `PointEntry` rows; corrections, refunds and redemptions are new entries. Balance = `SUM(amount)`.
- **Points are idempotent per string.** The unique key `(source, string_key, kind)` means each string earns translation points once and review points once, and re-syncing is always safe.
- **Redemptions** reserve points and stock in one transaction using `select_for_update`. Rejecting or cancelling a redemption writes a refund entry.
- **`shop.services` owns every redemption state change.** Each runs in one transaction with `select_for_update` (variant + user row on request; redemption on transitions) and writes a `RedemptionEvent`. Points move only via ledger entries (`REDEMPTION` −cost, `REFUND` +cost). Never edit `Redemption.status` directly.
- **E-mail is fire-after-commit** (`shop.notify.after_commit`). Failures are recorded as `RedemptionEvent` notes, never raised.
- **Photos** go to a public Vercel Blob store via `shop.blob.upload_product_image` (needs `BLOB_READ_WRITE_TOKEN`); the model only stores `image_url`.
- **Shipping addresses are personal data (LGPD).** They are wiped automatically after delivery.

## Secrets

The repo is public. All credentials come from environment variables (`.env`, gitignored; template in `.env.example`). Raw API responses go in `spike/` (gitignored). Scrub fixtures of tokens and real user data before committing them.
