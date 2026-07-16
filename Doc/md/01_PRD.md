# QuickBite AI + Loyalty
## Product Requirements Document (PRD) · v2.0

> **Document 1 of 6** · Confidential · Includes OTP Customer Auth + Stitch.ai Frontend · Research-informed via Consensus.app

---

## What Is This Doc?

This is the starting point of the entire app. Before anyone writes a single line of code or designs a single screen, this document answers the most important question — **what are we building and why?**

v2.0 adds: Customer OTP authentication (phone + email), seamless registration when a loyalty customer is new, encrypted profile storage, and Stitch.ai as the frontend platform — all backed by Consensus.app peer-reviewed research.

| Section | What It Answers |
|---------|----------------|
| → Problem Statement | What problem the app solves. Who faces it. Why it matters. Plain English. |
| → Target Users | Who is this for? Their goals, frustrations, and how they prefer to authenticate. |
| → Product Vision | One or two lines — the north star. |
| → Core Features | Every feature with must-have vs nice-to-have + which tier unlocks it. |
| → Subscription Tiers | Starter / Pro / Enterprise — feature limits, pricing, OTP options per tier. |
| → App Flow | Step-by-step: customer review + OTP loyalty journey, owner dashboard, upgrade flow. |
| → Success Metrics | Review metrics + loyalty metrics + SaaS metrics including OTP adoption rate. |
| → What We Are NOT Building | Scope boundaries for v1 to prevent drift. |

---

## 1. Problem Statement

### The Three-Sided Problem

| Problem | Title | Description |
|---------|-------|-------------|
| 🔴 Problem 1 | Blank Page Syndrome | Customer has a great meal. Opens Google Maps. Stares at the blank review box. Closes the app. Restaurant gets nothing. |
| 🟡 Problem 2 | Response Fatigue | Reviews arrive. Owner knows replies boost Google rankings. But they're running a restaurant. Reviews go unanswered for weeks. |
| 🟢 Problem 3 | No Persistent Loyalty | Customers collect stamps anonymously — so they can't track them across visits. Physical punch cards get lost. No data. No retention system. |

### Why Existing Solutions Fail

| Existing Solution | Why It Falls Short |
|------------------|--------------------|
| Physical punch / stamp cards | Lost easily. No data. No tracking. Staff must stamp manually. Cannot link to a returning customer. |
| Loyalty apps requiring download | 50%+ of customers refuse to install another app. Friction kills adoption before the first stamp is collected. |
| Static password loyalty accounts | Customers won't remember another password. High abandonment. No value proposition for 'yet another account'. |
| Google review requests (manual) | Unscalable. No AI to help customers write. No loyalty integration. Owner messages customers one by one. |
| Multiple disconnected tools | One tool for reviews, another for loyalty, another for SMS. Three dashboards, three subscriptions. |

### Why It Matters — The Numbers

- 93% of consumers say online reviews impact their dining decisions (BrightLocal, 2024)
- Businesses responding to reviews earn 35% more revenue (Harvard Business Review)
- Acquiring a new customer costs 5–7× more than retaining an existing one. Loyalty programs increase repeat visits 20–40% (Kaur, 2024 via Consensus.app)
- 74% of Indian smartphone users say they'd join a QR loyalty program if no app download was required
- Only 1 review per 80 customers — most great experiences go completely unrecorded
- OTP authentication eliminates the 'I forgot my password' abandonment problem — single-step login from any device (Acosta Mayorga et al., 2025 via Consensus.app)

> ✅ **QuickBite AI + Loyalty solves all three problems in one platform. One QR code. One scan. Review drafted. Loyalty stamp collected. OTP login for returning customers — no password to remember, no app to install.**

---

## 2. Target Users

### Persona 1 — Restaurant Owner
**Marco, 42 — owns two Italian restaurants in Pune**

| Attribute | Detail |
|-----------|--------|
| Tech Level | Moderate — WhatsApp, Instagram, Google Maps, Zomato daily |
| Primary Goal | More 5-star reviews AND returning customers — zero manual effort |
| Biggest Pain | No time for reputation management. No loyalty system. No way to know who came back. |
| How QuickBite Fixes It | AI handles review responses. OTP stamps bring customers back automatically. |
| Auth Method | Email + Password + Google Authenticator (TOTP) — enforced |
| Suggested Plan | Pro Tier |

### Persona 2 — Restaurant Manager
**Priya, 31 — front-of-house manager at a 4-location café chain**

| Attribute | Detail |
|-----------|--------|
| Tech Level | High — SaaS dashboards, Slack, analytics tools |
| Primary Goal | Consistent brand voice in responses + visibility into loyal customer data |
| Biggest Pain | Staff reply inconsistently. No real-time loyalty scan data by branch. |
| How QuickBite Fixes It | AI drafts ensure consistent tone. Dashboard shows live stamp counts. |
| Auth Method | Email + Password + TOTP MFA — enforced |
| Suggested Plan | Enterprise Tier |

### Persona 3 — Restaurant Diner / Customer ⭐ KEY PERSONA
**Ananya, 27 — food lover, frequent Google Maps and Zomato user**

| Attribute | Detail |
|-----------|--------|
| Tech Level | Very high — 10+ apps daily, comfortable with QR codes and OTP SMS |
| Primary Goal | Share great experiences + earn rewards without downloading yet another app |
| Biggest Pain | Blank review box. Physical stamp cards get lost. Doesn't want another password. |
| How QuickBite Fixes It | 30 seconds: stars + tags → AI writes review. OTP from SMS — no password ever. |
| **Auth Method** | **Phone OTP or Email OTP — no password required** |
| Suggested Plan | No account needed for review; OTP login for loyalty tracking |

### Persona 4 — Platform Super Admin
**QuickBite Internal Team — manages all restaurant tenants**

| Attribute | Detail |
|-----------|--------|
| Tech Level | Very high — engineering and operations background |
| Primary Goal | Manage tenant health, security, billing, fraud monitoring |
| Biggest Pain | No single platform view of all tenants, usage, OTP fraud, or subscriptions. |
| How QuickBite Fixes It | Super Admin panel with full audit logs, OTP fraud alerts, force-logout, billing override. |
| Auth Method | Email + Password + TOTP MFA — enforced |
| Suggested Plan | Internal role |

---

## 3. Product Vision

> *"QuickBite AI becomes the reputation and loyalty backbone for every independent restaurant — where every meal ends with a five-star story, every returning customer is rewarded seamlessly, and every owner wakes up to a thriving online presence."*

| Vision Element | What It Means in Practice |
|---------------|--------------------------|
| "Reputation backbone" | An always-on AI system managing reviews — syncing from Google, drafting responses, posting on approval. Like a PR manager on autopilot. |
| "Loyalty backbone" | App-less QR stamp card. No download. Customer scans once → stamp logged → OTP login to track → WhatsApp alert when reward is ready. |
| "Every meal ends with a story" | The review experience is as polished as the food. Happens naturally via QR. AI writes it. Customer pastes to Google in 3 taps. |
| "Every returning customer rewarded" | Digital stamp cards work silently in the background. 10th visit → free coffee. No staff effort. No physical card. No app download. |
| "Subscription-fuelled growth" | Revenue grows with the restaurant. Starter for trials. Pro for growing restaurants. Enterprise for chains. |
| OTP-first customer auth | Customers authenticate with their phone number — no password to create, forget, or reset. Research-backed design decision. |

---

## 4. Core Features

### Must-Have (MVP — v1.0)

| ID | Feature | Description | Tier |
|----|---------|-------------|------|
| F-01 | Smart Review Composer | Public mobile page via QR. Star tap + tags → AI drafts natural review in < 3 seconds. Copy to Google Maps / Yelp / submit internally. No login needed. | All tiers |
| F-02 | Owner/Staff Role-Based Auth + MFA | 5-level RBAC. JWT + refresh rotation. TOTP MFA enforced for Owner/Manager. Brute-force lockout. Email verification. | All tiers |
| F-02B | ★ Customer OTP Login | Customers log into their loyalty account via 6-digit OTP sent to phone (Twilio SMS) or email (SendGrid). No password. 5-min TTL. 3-attempt limit. 2-min rate limit. | All tiers |
| F-02C | ★ Seamless Customer Registration | When OTP user not found → 1-screen registration with pre-filled phone, encrypted name/email storage, WhatsApp opt-in. Account created + OTP sent in one flow. | All tiers |
| F-02D | ★ Stitch.ai Frontend | All screens generated via Stitch.ai AI platform + wired to FastAPI via Jinja2. Reduces frontend dev time ~60%. | System-wide |
| F-03 | AI Review Response Drafting | Hourly GMB sync → AI drafts response for every unanswered review. Queued for Manager approval. Usage-capped by plan. | All tiers (capped) |
| F-04 | Manager Approval Workflow | All AI responses require Manager+ approval before posting. Staff read-only. Rejected drafts regenerated. | All tiers |
| F-05 | Google My Business Sync | OAuth 2.0 + incremental cursor. Fetches new reviews hourly. Posts approved responses to GMB. | All tiers |
| F-06 | Reputation + Loyalty Dashboard | Live: avg rating, review count, sentiment trend, response rate, pending approvals + live stamp counts, reward redemptions, scan volume. | All tiers |
| F-07 | QR Loyalty Stamp Cards | Digital loyalty cards via QR code. GPS geofence validates location. Stamp logged to customer account (if OTP logged in) or anonymously. | All tiers |
| F-08 | Subscription Gating Engine | All features gated by plan. Monthly usage counters tracked real-time per tenant. Yellow warning at 80%. Hard block + upgrade modal at 100%. | System-wide |
| F-09 | Multi-Tenant Architecture | Each restaurant is an isolated tenant with subdomain, PostgreSQL RLS data isolation, and independent billing. | System-wide |
| F-10 | Super Admin Panel | Platform-wide: all tenants, fraud logs, billing, audit logs, force-logout, manual GMB sync trigger. | Internal |

### Nice-to-Have (v1.1+)

| ID | Feature | Description | Tier |
|----|---------|-------------|------|
| F-11 | WhatsApp Reward Notifications | Automated WhatsApp message when reward unlocked. High open rates vs email in India. | Pro+ |
| F-12 | Scratch Card Gamification | Every 5th scan → virtual scratch card with random reward. | Pro+ |
| F-13 | AI Digital Menu | Branch-specific digital menu served via same QR. AI can answer menu questions. | Pro+ |
| F-14 | CRM Campaign Manager | SMS/email review-request campaigns. Dry-run preview before sending. | Pro+ |
| F-15 | Yelp Integration | Extend review sync and response workflow to Yelp. | Pro+ |
| F-16 | Analytics Export | CSV (Pro) + PDF (Enterprise) export of review data and loyalty history. | Pro+ |
| F-17 | White-Label Review Page | Remove QuickBite branding. Custom logo. Custom domain (Enterprise). | Pro+ |

---

## 5. Subscription Tiers

| Feature | Starter (Free) | Pro (₹2,999/mo) | Enterprise (Custom) |
|---------|:-----------:|:----------:|:-----------:|
| Locations | 1 | Up to 3 | Unlimited |
| AI Review Generations/mo | 30 | 200 | Unlimited |
| AI Response Drafts/mo | 30 | 150 | Unlimited |
| Team Members | 2 | 5 | Unlimited |
| OTP Login | Basic Phone OTP | Phone + Email OTP | WhatsApp OTP option |
| SMS Campaigns/mo | None | 200 SMS | Unlimited |
| Dashboard Analytics | 30-day | 90-day + CSV | Full + PDF + API |
| Scratch Cards | No | Yes | Yes |
| WhatsApp Rewards | No | Yes | Yes |
| Review Page Branding | QB Branded | Unbranded | Custom domain |
| Free Trial | — | 14 days | Custom POC |
| Price (USD) | $0/mo | ~$36/mo | Contact sales |

### Subscription Business Rules

- Usage counters reset on the 1st of each month at 00:00 UTC
- At 80% of any limit → yellow warning banner with upgrade CTA
- At 100% → hard block + upgrade modal showing current vs next tier
- Growth/Pro 14-day trial: full access. Day 14 → card charged or auto-downgrade to Starter
- Stripe webhook `invoice.payment_failed` → 3-day grace → downgrade to Starter if unpaid

---

## 6. App Flow

### Flow A — Customer Review + OTP Loyalty Journey

| # | Screen | What Happens | Decision / Branch |
|---|--------|-------------|-------------------|
| 1 | QR Scan | Camera scans QR on receipt → mobile web page opens. No app. No login. | Invalid QR → error + restaurant contact. |
| 2 | Geofence | GPS location validated against branch coordinates (PostGIS). | Outside geofence → 'Please visit the restaurant to collect your stamp.' |
| 3 | Star Rating | Full-screen 5-star tap. Restaurant name and logo shown. | 1–2 stars → option for private feedback only. |
| 4 | Experience Tags | Grid of tags. Tap up to 5. At least 1 required. | 0 tags → gentle prompt. |
| 5 | AI Draft | Natural review in < 3 seconds. Editable. Georgia serif font. | Customer can freely edit. |
| 6 | Share | Copy + Open Google Maps / Copy + Open Yelp / Submit Internally. | Internal → saved to dashboard only. |
| 7 | OTP Login Prompt | 'Track your stamps across visits → Log in with your phone number.' | Skip → anonymous stamp. Log in → OTP flow. |
| 8 | Phone/Email Entry | Customer enters phone or email. OTP sent within 10 seconds. | Phone not found → registration page. Phone found → OTP verify. |
| 9 | OTP Verify | 6-digit code entry. Auto-fill from SMS. Auto-submit on 6th digit. | 3 wrong attempts → OTP invalidated. Request new code. |
| 10 | Registration (if new) | Pre-filled phone. Name (required). Email (optional). WhatsApp opt-in. One tap. | Account created + OTP verified in single flow. |
| 11 | Stamp Collected | Animated 'Stamp Collected! 🎁' screen. Updated loyalty card shown. | If threshold reached → Reward Unlocked card shown. |
| 12 | WhatsApp Opt-in | 'Get reward alerts on WhatsApp?' (if Pro+ restaurant). | Opt-in → WhatsApp alert when next reward ready. |

### Flow B — Owner Onboarding + Dashboard

| # | Screen | What Happens | Decision / Branch |
|---|--------|-------------|-------------------|
| 1 | Register | Name, email, password, restaurant name. Live password strength meter. | Weak password → blocked. Duplicate email → 'Login here'. |
| 2 | Email Verify | Verification link sent. Account inactive until clicked. | Expired link → resend option. |
| 3 | Choose Plan | Starter / Pro (14-day trial) / Enterprise. Feature comparison shown. | Starter → skip billing. Pro → Stripe card. Enterprise → 'Book a demo'. |
| 4 | MFA Setup | Scan QR with Google Authenticator. Cannot skip for Owner. | Wrong code 3× → restart. Backup codes displayed. |
| 5 | Add Branch + GMB | Add branch with GPS coordinates + geofence radius. Connect GMB via OAuth. | Starter → 1 branch max. Pro → 3. More → upgrade prompt. |
| 6 | Configure Loyalty | Set reward rules: stamps_required, reward_type, validity_days. | Different program per branch. Active immediately. |
| 7 | Dashboard | Stats row + review list + pending AI responses + loyalty scan live feed. | 0 reviews + 0 scans → 'Download your QR code and print it on a receipt.' |
| 8 | Review Response | Review + AI draft side-by-side. Approve → posts to GMB < 5 min. | Staff → read-only. Manager → approve/reject. |
| 9 | QR Download | Print-ready PNG + PDF per branch. Stitch.ai-designed template. | Multiple branches → separate QR per branch. |

---

## 7. Success Metrics

| Metric | Target | Measurement | Cadence |
|--------|--------|-------------|---------|
| GMB Activation Rate | ≥ 60% | OAuth connection event per cohort | Weekly |
| Review Generation Rate | ≥ 25% | QR scan vs copy/submit events | Weekly |
| AI Response Approval Rate | ≥ 80% | Approval vs edit vs reject on review_responses | Weekly |
| ★ OTP Login Adoption Rate | ≥ 35% | customers rows created ÷ total QR sessions | Weekly |
| ★ Registered Loyalty Customer Count | ≥ 100 / branch / month | customers.created_at per tenant | Monthly |
| ★ Returning Customer Rate | ≥ 50% | customer_id in stamp_logs 2+ times in 30 days | Monthly |
| Daily Active Loyalty Scans | ≥ 15 / branch | stamp_logs rows per branch per day | Weekly |
| Reward Redemption Rate | ≥ 60% | redemption_code redeemed ÷ total issued | Monthly |
| Trial-to-Paid Conversion | ≥ 40% | Stripe subscription status at Day 14 | Weekly |
| Monthly Recurring Revenue (MRR) | ₹3L by Month 6 | Stripe MRR dashboard | Monthly |
| Platform Retention at Day 90 | ≥ 70% | Subscription status + last_login_at | Monthly |
| Monthly Churn Rate | < 5% | Stripe cancellations ÷ active count | Monthly |
| P95 API Latency | < 500ms | Prometheus / Locust | Per release |
| ★ OTP Delivery Success Rate | ≥ 98% | Twilio / SendGrid delivery webhooks | Weekly |

> 💡 **Anti-metric:** Do NOT optimise for raw QR scan count. Optimise for authenticated return visits. A restaurant with 50 returning OTP-logged-in customers outperforms one with 500 anonymous one-time scans.

---

## 8. What We Are NOT Building in Version 1

| Not Building | Why We Are Excluding It | When It May Come |
|-------------|------------------------|-----------------|
| Downloadable iOS / Android app | No-download = higher adoption. Our core innovation is zero-friction QR. A native app reintroduces the problem we solve. | Never — PWA is the strategy |
| Biometric authentication | Adds device dependency and native app requirement. OTP is universally accessible. | v2.0 evaluation — native app only |
| Password-based customer loyalty accounts | Customers won't remember another password. OTP is simpler, faster, and more secure (research-backed). | Never — OTP is the strategy |
| A replacement for Google Maps or Yelp | We are a management layer alongside review platforms, not a competing destination. | Never |
| Full CRM / email marketing suite | Campaign manager is review-request SMS only in v1.1. Full CRM delays core product. | v2.0 |
| Reservation or table booking system | OpenTable and Resy own this. Our focus is post-visit. | Out of scope permanently |
| Real-time owner–customer chat | Async reviews are right for restaurants. Chat burdens time-poor owners. | v2.0 evaluation |
| Points-based loyalty (spend-tracking) | Visit-based stamps are simpler with higher adoption. Points require POS integration. | v1.1 or v2.0 with POS |
| Multi-language AI responses | Requires per-language QA and legal review. Hindi + English planned for v1.2. | v1.2 |
| Blockchain loyalty tokens | High implementation complexity for marginal user benefit at this stage (Behrouzi et al., 2023 via Consensus.app). | v3.0 research item |

---

## 🤖 Prompt Used to Generate This Document

> *"Act as a senior product manager with experience in early-stage startups. I am building an app and I need you to create a detailed Product Requirements Document. Cover: what the app does, who it is for, what problem it solves (including lack of persistent loyalty tracking without secure login), all core features with must-have vs nice-to-have classification including OTP customer authentication (phone + email) with seamless registration when user not found, a full subscription tier model (Starter/Pro/Enterprise), how users flow through the app including the OTP login and registration journey, success metrics including OTP adoption rate and returning customer rate, and what we are deliberately NOT building in version one — including why password-based customer accounts are excluded."*

---

*Document 1 of 6 · QuickBite AI + Loyalty · v2.0 · Confidential*
