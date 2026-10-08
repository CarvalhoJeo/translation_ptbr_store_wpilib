# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Plan 1 (earning points: accounts, ledger, Transifex sync) is implemented. Plan 2 (catalog, redemptions, leaderboard, deploy) is not built yet. Design: `docs/superpowers/specs/2026-10-07-translation-store-design.md`. Plans: `docs/superpowers/plans/`.

## Commands

- Install: `uv sync`
- Run tests: `uv run pytest` · one test: `uv run pytest tests/test_ledger.py::test_same_event_twice_is_ignored -v`
- Dev server: `uv run python manage.py migrate && uv run python manage.py runserver`
- Admin user: `uv run python manage.py createsuperuser`
- Track all frc-docs resources: `uv run python manage.py track_resources`
- Sync points manually / self-hosted cron: `flock -n /tmp/sync_transifex.lock sh -c 'uv run python manage.py track_resources && uv run python manage.py sync_transifex'` (exits non-zero if any resource failed). The lock prevents overlapping runs, and `track_resources` picks up newly added frc-docs pages. If a resource is deleted on Transifex, deactivate it in the admin.

## Deployment (Vercel, Hobby plan)

- Django is auto-detected (`manage.py` → `store.wsgi`). `[tool.vercel.scripts] build` runs `migrate` on every build; Vercel runs `collectstatic` itself.
- Postgres is Neon via the Vercel Marketplace (`DATABASE_URL` injected). SQLite does not persist on Vercel.
- Vercel Cron calls `GET /cron/sync/` daily (Hobby allows only daily crons; `vercel.json`). The view checks `Authorization: Bearer $CRON_SECRET`, runs `track_project_resources`, then `sync_all` with a `SYNC_TIME_BUDGET_SECONDS` (240 s) deadline under the 300 s function limit; resources not reached go first next run (ordered by `last_synced_at`).
- On Vercel (`VERCEL=1`) `.env` is ignored and a missing `DJANGO_SECRET_KEY` fails startup. `*.vercel.app` hosts come from `VERCEL_URL` / `VERCEL_PROJECT_PRODUCTION_URL` automatically.
- Deploy: `vercel deploy --prod`. Production env vars: `vercel env ls production`.

## What this is

A reward store for the WPILib pt-BR translation team on Transifex. Translators earn points for words translated and reviewed in Brazilian Portuguese (Transifex language code `pt`, not `pt_BR`; project `o:wpilib:p:frc-docs`), only after `LAUNCH_AT`, and redeem them for physical swag. Admins approve account links and redemptions. Stack: Django + Postgres, server-rendered pt-BR UI, GitHub login (django-allauth).

## Architecture rules that span multiple apps

- **Progress comes from polling the free Transifex API** (`GET /resource_translations`), not Activity Reports, which are paid-only. `transifex.sync.sync_all` (called by the `/cron/sync/` view or the `sync_transifex` command) turns per-string changes into points.
- **`transifex.ProgressSource` is the boundary.** It yields normalized `ProgressEvent`s, and the ledger must never handle raw Transifex JSON.
- **The ledger is append-only.** Never update or delete `PointEntry` rows; corrections, refunds and redemptions are new entries. Balance = `SUM(amount)`.
- **Points are idempotent per string.** The unique key `(source, string_key, kind)` means each string earns translation points once and review points once, and re-syncing is always safe.
- **Redemptions** reserve points and stock in one transaction using `select_for_update`. Rejecting or cancelling a redemption writes a refund entry.
- **Shipping addresses are personal data (LGPD).** They are wiped automatically after delivery.

## Secrets

The repo is public. All credentials come from environment variables (`.env`, gitignored; template in `.env.example`). Raw API responses go in `spike/` (gitignored). Scrub fixtures of tokens and real user data before committing them.
