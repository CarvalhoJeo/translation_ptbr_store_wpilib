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
- Sync points (cron, every 30–60 min): `uv run python manage.py sync_transifex` (exits non-zero if any resource failed)

## What this is

A reward store for the WPILib pt-BR translation team on Transifex. Translators earn points for words translated and reviewed in Brazilian Portuguese (Transifex language code `pt`, not `pt_BR`; project `o:wpilib:p:frc-docs`), only after `LAUNCH_AT`, and redeem them for physical swag. Admins approve account links and redemptions. Stack: Django + Postgres, server-rendered pt-BR UI, GitHub login (django-allauth).

## Architecture rules that span multiple apps

- **Progress comes from polling the free Transifex API** (`GET /resource_translations`), not Activity Reports, which are paid-only. A cron-run management command, `sync_transifex`, turns per-string changes into points.
- **`transifex.ProgressSource` is the boundary.** It yields normalized `ProgressEvent`s, and the ledger must never handle raw Transifex JSON.
- **The ledger is append-only.** Never update or delete `PointEntry` rows; corrections, refunds and redemptions are new entries. Balance = `SUM(amount)`.
- **Points are idempotent per string.** The unique key `(source, string_key, kind)` means each string earns translation points once and review points once, and re-syncing is always safe.
- **Redemptions** reserve points and stock in one transaction using `select_for_update`. Rejecting or cancelling a redemption writes a refund entry.
- **Shipping addresses are personal data (LGPD).** They are wiped automatically after delivery.

## Secrets

The repo is public. All credentials come from environment variables (`.env`, gitignored; template in `.env.example`). Raw API responses go in `spike/` (gitignored). Scrub fixtures of tokens and real user data before committing them.
