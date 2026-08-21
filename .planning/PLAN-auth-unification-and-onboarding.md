# Plan: Unified auth + business onboarding

Branch: feature/auth-04-rbac-roles
Status: DRAFT — awaiting go-ahead on Ticket 1

## What already exists (verified against code, not assumed)

- `RoleLevel.USER = 6` — a verified, tenant-less role for "logged in, hasn't
  registered a business yet." [app/core/rbac.py:23-28]
- `/auth/identify` + `identity_service.classify_login_identifier()` — already
  the identify-first shared login lookup: takes one identifier, tells the
  caller whether it's an Owner/Staff account or a Customer account, so a
  single login screen can render the right method. [app/services/identity_service.py]
- `AuthService.become_restaurant()` / `POST /auth/register-restaurant` —
  already does spec steps 3-5 almost verbatim: takes `restaurant_name` +
  `plan_id`, creates the `Tenant`, promotes the caller's role to `OWNER`,
  revokes stale tokens, re-issues a session. This is the "Join Us" backend,
  already built. [app/services/auth_service.py:281-372]
- `Principal` + `POST /auth/link/{provider}` — Supabase and Firebase OAuth
  already supported, linked onto an existing local account.
  [app/core/principal.py], [app/api/v1/routers/auth.py:221-263]
- Customer OTP auth already exists as a fully separate stack: `customer.customers`
  table, `CUSTOMER_SECRET_KEY`, `customer_auth.py`, `customer_security.py`.

## Gaps vs. the spec

1. Owner/Staff registration is email+password only — no OTP, no
   OAuth-as-primary-registration.
2. No shared login page UI (backend for it already exists via `/auth/identify`).
3. No "Join Us" navbar entry or business-registration page UI.
4. `become_restaurant()` doesn't OTP-verify a second contact method and sends
   no "join" success email/SMS — needs adding.
5. `SubscriptionPlan` (app/db/models/subscription.py) has **no category
   column** — it's flat starter/pro/enterprise. "Show plans relevant to
   chosen category" is new schema work, not just UI wiring.

## Premise correction (confirmed with user)

The spec's "one common login page for everyone" is being built as **one
shared login UI that routes to the existing separate USER and CUSTOMER auth
backends** — not a merged identity/session model. `Principal` deliberately
keeps `SubjectType.USER` (restaurant.users, `SECRET_KEY`) and
`SubjectType.CUSTOMER` (customer.customers, `CUSTOMER_SECRET_KEY`) apart;
AGENTS.md requires the two JWT keys stay different. Merging the UI is safe;
merging the identity model is a separate, security-review-gated decision
that is explicitly OUT of scope here.

Deletion scope (confirmed): remove only old templates/JS
(`app/templates/customer/login.html`, `register.html`, `verify_email.html`,
`static/js/auth-login.js`, `auth-register.js`) — keep and adapt backend
services/routes.

## Ticket breakdown (sequential, each its own PR)

### Ticket 1 — Auth unification (shared login/register UI)
- New shared login template + JS wired to `/auth/identify`, then to
  `/auth/login` (password/MFA) or `/customer/auth/*` (OTP) based on the
  identify response.
- Owner/Staff registration form gains an OTP path alongside password
  (requires: new phone/email OTP send+verify endpoints on the USER side —
  currently OTP infra only exists for customers, so this is new service code,
  not reuse).
- Delete old templates/JS listed above once the new page covers their cases.
- Security-sensitive (auth, OTP) — flagged for Security Dev review per
  AGENTS.md.

### Ticket 2 — Join Us + business registration
- Navbar "Join Us" button (customer session, role USER only).
- Business registration page: business name + contact email/phone, pre-fill
  known contact method from the logged-in identity, OTP-verify the other.
- Extend `become_restaurant()` to require the second-contact OTP proof before
  creating the Tenant.
- Security-sensitive (OTP, PII) — flagged for Security Dev review.

### Ticket 3 — Category-scoped subscription plans + purchase
- Add a category dimension to `SubscriptionPlan` (new Alembic migration) or
  confirm categories map to existing `feature_limits` JSONB instead of a new
  column — needs a decision before planning this ticket in detail.
- Wire plan selection UI to `register-restaurant`'s existing `plan_id` param.
- "Join" success email/SMS after purchase completes (missing today).

### Ticket 4 — UI polish + QA
- Run `/ui-ux-pro-max`, `/impeccable`, animation-vocabulary-informed motion
  polish on the finished flow.
- Manual QA pass (browser) of: register → verify → login → Join Us → business
  reg → OTP verify second contact → plan purchase → role becomes OWNER →
  confirmation email/SMS sent.

## Next step

Awaiting go-ahead to start Ticket 1 implementation (schemas first, then
service, then routes, then templates/JS — per CLAUDE.md workflow).
