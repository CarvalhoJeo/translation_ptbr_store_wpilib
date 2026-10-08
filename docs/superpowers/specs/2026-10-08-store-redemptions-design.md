# Plan 2 — Store, Redemptions, Ranking — Design

## Context

Plan 1 (earning points) is live at https://translation-ptbr-store-wpilib.vercel.app (Django on Vercel Hobby, Neon Postgres, daily cron sync, GitHub login). Admins can approve Transifex links, but nothing can be spent yet. This plan adds the catalog, the redemption flow, e-mail notifications, and a public ranking. The UI is pt-BR and uses the existing playful theme (`static/css/site.css`).

Base spec: `docs/superpowers/specs/2026-10-07-translation-store-design.md`. This document extends it and overrides it where they differ.

## Decisions (user, 2026-10-08)

- **Delivery:** the translator chooses **Correios** (mail) or **em mãos** (handed over in person). Each product says which of the two it allows.
- **Shipping cost (Correios)** is paid by the translator **in money via Pix, outside the site**. The admin ships only after confirming payment. The site only records "frete pago".
- **Photos:** uploaded in the admin and stored in **Vercel Blob** (public store).
- **Variants** (e.g. shirt sizes) have **their own stock**.
- **No per-person limit.** Each redemption is **one item**; there is no cart.
- **E-mail** goes to admins on every new redemption, and to the translator on approved, shipped, ready-for-pickup and rejected. It is sent through a **team Gmail account over SMTP**, because Resend needs a verified domain and `*.vercel.app` can't be verified.
- **Transifex origins:** every origin earns full points, uploads and MT included. No code change.
- **Approach:** everything stays inside the existing Django project as a new app `store`. Shopify and a separate frontend were rejected.

## Data model (app `store`)

**`Product`**
- `name`
- `description` (text)
- `cost` (positive int, points)
- `image_url` (URL, blank allowed)
- `active` (bool)
- `position` (int, display order)
- `allows_mail` (bool)
- `allows_pickup` (bool); at least one of the two must be true (validated)

**`Variant`**, FK to `Product` (related_name `variants`)
- `name` (e.g. "M"), `stock` (int ≥ 0, DB check constraint), `active`
- Every product has ≥ 1 variant. On save, a product with no variants gets one named **"Único"**, so stock always lives on variants.
- In the admin, variants are edited inline on the product.

**`Redemption`**
- `user`; `variant` (FK, PROTECT)
- `cost` (points frozen at request time)
- `delivery` (`mail` | `pickup`)
- `status` (see the state machine)
- `ledger_entry` (FK to the `PointEntry` debit)
- Mail-only address fields: `full_name`, `cep`, `street`, `number`, `complement`, `district`, `city`, `uf`, `phone`
- `tracking_code`
- `admin_note`, shown to the translator
- `created_at`, `updated_at`, `delivered_at`, `address_erased_at`

**`RedemptionEvent`** (history): `redemption`, `status_from`, `status_to`, `actor` (user or null for system), `note`, `created_at`.

**`StoreSettings`** (singleton, editable in the admin, `current()` like `PointRates`): `pix_instructions` (text that goes in the approval e-mail for Correios orders, e.g. Pix key and value or "a combinar").

**Points:** a redemption writes a `PointEntry(kind=REDEMPTION, amount=-cost, note=...)`. Reject or cancel writes `PointEntry(kind=REFUND, amount=+cost)`. The ledger stays append-only. `string_key` stays blank for these kinds, so the existing unique constraint doesn't apply.

## Redemption state machine

```
requested ──approve──► approved ──(mail) mark_paid──► shipping_paid ──ship(tracking)──► shipped ──deliver──► delivered
    │                     └──(pickup) ready(note)──► ready_for_pickup ──deliver──► delivered
    ├──reject(note)──► rejected      (refund points + restore stock)
    └──cancel (by translator)──► cancelled   (refund points + restore stock)
```

- Transitions are only allowed along the arrows. Any other one raises `RedemptionError`, with a pt-BR message.
- Only `requested` can be rejected (admin) or cancelled (the translator who owns it).
- Every transition writes a `RedemptionEvent` inside the same transaction.
- **Request** happens in one `transaction.atomic()`:
  - `select_for_update` locks the variant and the user row (serializing that user's balance check).
  - It checks: approved Transifex link; product and variant active; product allows the chosen delivery; `stock ≥ 1`; `balance ≥ cost`; all address fields filled when the delivery is mail.
  - It then decrements stock, writes the debit entry, and creates the redemption with an initial event.
- **Refund** (reject or cancel) happens in one transaction: it writes the refund entry and restores stock (`F("stock") + 1`).

## Translator UI

- **`/loja/`:** product cards (photo, name, description, cost), with sizes shown as chips and out-of-stock ones disabled. A product whose variants are all at zero shows as **"Esgotado"**. The button says **"Resgatar"**, or "Você precisa de N pontos" when the balance is too low, or "Vincule sua conta" when there is no approved link.
- **`/loja/<id>/resgatar/`** (login required): a form with variant, delivery (only the allowed options) and address fields (shown when the delivery is Correios). CEP format is validated (`00000-000`), as is the UF (one of the 27). Confirming shows a toast and redirects to Minha conta.
- **Minha conta**, new section **"Meus resgates"**:
  - status pill
  - tracking code (linked to the Correios tracking page)
  - admin note
  - a **Cancelar** button while the order is `requested`

  Also new on Minha conta:
  - an **"E-mail para avisos"** field, editable, defaulting to the GitHub e-mail;
  - for a PENDING link, a line **"N pontos guardados aguardando aprovação"** (the sum of that username's `UnclaimedEvent`s), plus the ability to **correct the Transifex username** while it's pending.
- **`/ranking/`** (public): translators with an approved link, ordered by **points earned**. That means the sum of positive `PointEntry` amounts of kinds translated, reviewed and adjustment; redemptions and refunds are excluded. Each row shows the GitHub username. A toggle switches between **"Total"** and **"Este mês"**.

## Admin

- **`ProductAdmin`:**
  - variant inline
  - an image file field (`photo_upload`, not stored on the model) that uploads to Blob on save and sets `image_url`
  - a list showing cost, total stock and active
- **`RedemptionAdmin`:**
  - filters by status and delivery; search by user and tracking code
  - read-only history inline; address fields read-only once erased
  - **actions** that call the services: Aprovar, Recusar (asks for a note), Marcar frete pago, Marcar enviado (asks for the tracking code), Pronto para retirar (asks for a note), Marcar entregue
  - rows not in the right status are skipped with a per-row error message
  - multi-field input (note, tracking) uses an intermediate form page
- **`StoreSettingsAdmin`:** a singleton.
- **Plan 1 carry-over:**
  - pt-BR `verbose_name`s: "Perfil/Perfis", "Lançamento de pontos", "Evento pendente/Eventos pendentes"
  - the adjustment note is now **required**
  - the `has_*_permission` overrides call `super()`

## Photos (Vercel Blob)

- Provision a **public** Blob store connected to the project (Production + Preview + Development). The store provides `BLOB_READ_WRITE_TOKEN` and `BLOB_STORE_ID`.
- `store/blob.py`: `upload_product_image(file) -> str` uses the official `vercel` Python SDK (`vercel.blob`). The path is `products/<slug>-<random>.<ext>` and access is public.
  - Accepts JPEG, PNG and WebP up to **4 MB** (under the 4.5 MB request limit).
  - Validated by content type and by Pillow opening the image.
- Tests mock the upload function; there are no network calls.

## E-mail (Gmail SMTP)

- Django's SMTP backend: `EMAIL_HOST=smtp.gmail.com`, `EMAIL_PORT=587`, TLS.
- Env vars:
  - `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (a Gmail **app password**, a Sensitive variable on Vercel)
  - `DEFAULT_FROM_EMAIL` ("Loja de Traduções WPILib <...>")
- Without `EMAIL_HOST_USER`, the console backend is used, which covers local dev and tests (tests use locmem).
- `store/notify.py` holds one function per event. Templates live in `templates/emails/*.txt` (pt-BR, plain text).
  - **Admin "novo resgate":** to every `is_staff` user with an e-mail. It includes the product, variant, delivery and a link to the admin change page.
  - **Translator:**
    - **approved:** for Correios it includes `StoreSettings.pix_instructions`; for pickup it says "vamos combinar a entrega"
    - **shipped:** includes the tracking code and link
    - **ready_for_pickup:** includes the note
    - **rejected:** includes the note and "seus pontos foram devolvidos"
- **Sending never breaks the action.** E-mail is sent after the transaction commits (`transaction.on_commit`). Failures are logged and recorded on the redemption as a `RedemptionEvent` with note "falha ao enviar e-mail: …", visible in the admin.
- The translator's address comes from `user.email`, editable in Minha conta. The GitHub login requests scope `user:email` so allauth can fill in the primary verified e-mail.

## Cron additions (same daily `/cron/sync/`)

- After the sync: erase all address fields (`full_name`, `cep`, `street`, `number`, `complement`, `district`, `city`, `uf`, `phone`) of redemptions with `delivered_at` older than **30 days**, and set `address_erased_at`. The tracking code, status and history remain.
- The sync locks each resource with `select_for_update(skip_locked=True)` inside its transaction, so an overlapping run skips resources already in progress.

## Error handling

- All business-rule failures raise `RedemptionError` with a pt-BR message. Views show it as a form error, and admin actions show it as a per-row message.
- Concurrent requests for the last unit: one succeeds; the other gets "Esgotou enquanto você pedia."
- Concurrent requests that together exceed the balance: one succeeds; the other gets "Saldo insuficiente."

## Testing

- pytest + pytest-django, following Plan 1's style.
- **Services:**
  - every allowed transition, and the rejection of each disallowed one
  - refund correctness (balance and stock)
  - frozen cost after a price change
  - mail address required; pickup-only products reject mail
  - an inactive variant or product can't be requested
- **Concurrency:**
  - two threads requesting the last unit, or more than the balance allows
  - this needs real Postgres row locks, so it runs only when `TEST_DATABASE_URL` points at Postgres (marked `postgres`)
  - CI and local runs default to SQLite and skip it
- **Views:** catalog states (esgotado, insufficient points, no link), the request form validation (CEP, UF), cancel, the ranking ordering and the month filter.
- **Admin actions:** each action through the admin client, including the intermediate forms.
- **E-mail:** the locmem outbox contents per event; a failure is recorded as an event and the action still commits.
- **Cron:** address erasure after 30 days and not before.
- **Blob:** a mocked upload sets `image_url`; invalid or oversized files are rejected.

## Out of scope

- a cart or multiple items per redemption
- online payment or shipping calculation
- non-pt-BR languages
- automatic Correios tracking updates
- per-person limits
