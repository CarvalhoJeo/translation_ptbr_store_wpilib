# WPILib pt-BR Translation Rewards Store — Design

## Context

Luca coordinates the WPILib pt-BR translation team on Transifex and wants to motivate contributors with a store where translation progress earns points that can be redeemed for **physical swag**. The repo (`~/development/translation_ptbr_store_wpilib`) is empty apart from a placeholder `CLAUDE.md`.

**What the user decided**
- Rewards: physical swag (needs shipping/hand-off, so a request → approve → fulfill flow).
- Earning: words translated + words reviewed (review earns less than translation).
- Scale: small team (< ~30 translators), 2–4 admins.
- Login: GitHub OAuth; user enters their Transifex username; an admin approves the link once.
- Stack: Python / Django.
- Only work done **after launch** earns points (no backfill).
- Progress source: poll the free Transifex API (approach 1).

**Assumptions (correct me)**
- UI in pt-BR.
- Only the pt_BR language of WPILib projects counts; the admin picks which Transifex projects/resources are tracked.
- Point values are admin-configurable; the starting values are 2 pts/translated word and 1 pt/reviewed word.

**Key research finding:** Transifex's per-user Activity Reports API is paid-only (Growth plan and up); open-source orgs have been refused it. The basic `GET /resource_translations` endpoint (filters: `resource` + `language` required, `date_translated[gt|lt]`, `translator`, `reviewed`…; relationships: `translator`, `reviewer`, `proofreader`, `resource_string`; timestamps `datetime_translated`/`datetime_reviewed`; 150/page) gives per-string attribution, so the store works out credit itself. The org's plan is unknown, so step 0 is a spike that confirms the endpoint works with Luca's token.

## Architecture

One Django project, server-rendered templates, Postgres, plus one scheduled management command.

```
store/          Django project settings
accounts/       Profile (GitHub user ↔ Transifex username, link status)
transifex/      API client + ProgressSource interface + sync command
ledger/         PointEntry (append-only) + balance queries + point rules
catalog/        Item (name, photo, cost, stock, active)
redemptions/    Redemption (status machine) + shipping info
```

**Isolation boundary:** `transifex.ProgressSource` yields normalized `ProgressEvent(tx_username, kind: translated|reviewed, string_key, words, occurred_at)`. The ledger never sees Transifex JSON. A future `ActivityReportSource` would implement the same interface.

## Data flow

### Sync (cron every 30–60 min: `manage.py sync_transifex`)
1. For each tracked resource: `GET resource_translations?filter[resource]=…&filter[language]=l:pt_BR&filter[date_translated][gt]=<cursor>&include=resource_string`, paging through `links.next`.
2. Also fetch `filter[reviewed]=true` with a datetime window, and keep entries whose `datetime_reviewed` is after the cursor. (The spike checks whether a review-date filter exists.)
3. Emit events only for timestamps ≥ `LAUNCH_AT`. Word count = whitespace-split words of the source string after stripping placeholders/markup. Simple and predictable; the spike checks it against Transifex's own counts.
4. For each event: look up the Profile by Transifex username. If one is **approved**, insert a `PointEntry`. If not, store it in `UnclaimedEvent` and credit it automatically when the link is approved, so people who sign up late lose nothing earned after launch.
5. Advance the per-resource cursor only after the whole page set commits (one transaction per resource).

**Idempotency:** `PointEntry` has a unique key `(source, string_key, kind)`. A string earns translation points once and review points once. Re-translating a string doesn't earn again (prevents farming), and re-syncs are safe.

**Known limitation:** Transifex stores only the latest translator per string. If two people touch the same string within one sync interval, the last one gets credit. This is acceptable at this team size.

### Ledger
- `PointEntry(user, amount ±int, kind: translated|reviewed|redemption|refund|adjustment, ref, note, created_at)`. Entries are never edited; corrections are new `adjustment` entries made by admins.
- Balance = `SUM(amount)`. Each entry's amount is computed when it is inserted, so changing the rates only affects future events.

### Redemption
- States: `requested → approved → shipped → delivered`, or `requested → rejected` / `cancelled`.
- On request, in one DB transaction with `select_for_update` on the user's entries and the item: check balance ≥ cost and stock > 0, insert a `-cost` `redemption` entry, and decrement stock. Rejecting or cancelling inserts a `refund` entry and restores stock.
- Shipping address is collected on the request form. Because of LGPD, it is wiped automatically N days after `delivered` (a management command running on the same cron).
- Admins get an email or Discord webhook notification on new requests (optional, phase 2).

## Pages
- Public: leaderboard (points earned, not balance), catalog.
- Translator: my points history, my redemptions, link-Transifex form.
- Admin: Django admin for items, link approvals, redemptions (with list filters and bulk "mark shipped" actions), manual adjustments, tracked resources, sync status/last error.

## Error handling
- API 429/5xx: retry with backoff. If a resource fails, log it, keep its cursor, and continue with the others. The last sync result is shown in the admin.
- An invalid token or plan restriction (403) gives a clear error in the admin, and no cursor moves.

## Testing
- pytest + pytest-django.
- The Transifex client is tested against recorded JSON fixtures (from the spike) using `responses`/`respx`.
- Sync tests check idempotency (running twice gives the same balance), the launch-date cutoff, crediting unclaimed events on link approval, and that a failed resource leaves its cursor unchanged.
- Redemption tests check insufficient balance, out-of-stock, the refund on reject, and concurrent requests (no negative balance).

## Delivery steps
0. **Spike (throwaway):** with Luca's API token, call `resource_translations` for one WPILib pt_BR resource. Confirm access on the org's plan, the project/resource slugs, the available review-date filter, and word-count behavior. Save the responses as test fixtures.
1. Scaffold Django + Postgres + allauth GitHub, with pt-BR locale.
2. Accounts + Transifex linking + admin approval.
3. Ledger + Transifex client + sync command (TDD against fixtures).
4. Catalog + redemption flow + address wipe.
5. Leaderboard and translator pages.
6. Deploy: a small host running Django + cron (Render/Fly/Railway/VPS, chosen when we get there), with Postgres.

**Repo & secrets (user request):** `git init`, create a **public** GitHub repo under Luca's account, and push. Before the first commit:
- `.gitignore` covers `.env`, `*.env`, `db.sqlite3`, `media/`, `.venv/`, `__pycache__/`, and `spike/` raw responses.
- All secrets (Transifex token, GitHub OAuth client secret, `SECRET_KEY`, DB URL) are read from environment variables via `.env`. Only a placeholder `.env.example` is committed.
- Spike fixtures are scrubbed of tokens and real users' emails/IDs before they're committed as test data.
- Run a secret scan of the staged files (grep for token patterns plus `git diff --cached` review) before every commit/push.

Each step after the spike gets its own detailed plan via writing-plans. After approval, this design is also saved as `docs/superpowers/specs/2026-10-07-translation-store-design.md` in the repo, and `CLAUDE.md` is updated.

## Verification
- `pytest` green, including the sync idempotency and concurrency tests.
- End to end locally: run `sync_transifex` against the real API for one resource → points show up for a linked test account → redeem an item → approve/reject it in the admin → the balance and stock are correct.
