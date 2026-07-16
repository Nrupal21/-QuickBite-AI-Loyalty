# QuickBite AI + Loyalty
## Developer & AI Agent Working Guide · v1.0

> Complete build playbook for developers and AI agents — AI tools, IDEs, branch workflow, hour estimates, progress reports, and all latest files

---

## Section 1. AI Agents for QuickBite Development

> **Rule for every AI agent session:** Start with — *"I am building QuickBite AI + Loyalty. Read the Technical Architecture Document first. All code must follow the folder structure in Doc 2."* Then paste the relevant doc section.

### Full Agent List

| # | Agent / Tool | Type | Best For in QuickBite | How to Use |
|---|-------------|------|-----------------------|------------|
| 1 | **Claude (Anthropic)** | Architecture + Code | System design, security review, code review, complex refactors, RLS policy reasoning | Share Doc 2 + Doc 3 at session start. Best for OTP security, RLS, and multi-step reasoning. |
| 2 | **GitHub Copilot** | IDE autocomplete | Inline code completion — SQLAlchemy queries, Celery tasks, Tailwind classes | Install in VS Code. Works best with the correct file open for context. |
| 3 | **Cursor** | AI IDE | Full codebase-aware coding — writes entire feature files, refactors across files | Use as primary IDE. `Cmd+K` to edit, `Cmd+L` to chat. Add Doc 2 + Doc 3 as `@` context. |
| 4 | **Windsurf (Codeium)** | AI IDE | Free alternative to Cursor. Cascade agent for multi-file changes | Good for solo devs on a budget. Cascade: *"Implement LOYALTY-03 geofence per the schema in branch.py"* |
| 5 | **Devin (Cognition)** | Autonomous agent | Multi-step ticket implementation end-to-end | Give it a single ticket from Doc 5 with acceptance criteria. It runs tests and iterates. |
| 6 | **Aider (CLI agent)** | CLI coding agent | Terminal-based multi-file editing with git. `aider --model claude-3-5-sonnet` | Run: `aider --model claude-3-5-sonnet` then `/add app/services/customer_otp_service.py` |
| 7 | **OpenAI o3 / ChatGPT** | Complex reasoning | Algorithmic problems, PostGIS query optimisation, JWT security edge cases | Use when you need step-by-step reasoning. *"Explain how RLS interacts with async SQLAlchemy sessions"* |
| 8 | **Stitch.ai** | Frontend generation | Generate all Jinja2 + Tailwind HTML screens from text prompts | See Doc 4 for 12-screen prompt guide. Import QuickBite design system as workspace defaults. |
| 9 | **v0.dev (Vercel)** | React UI generation | Generate complex UI components as React alternatives | Export as HTML + Tailwind if staying with Jinja2. |
| 10 | **bolt.new** | Full-stack generation | Bootstrap entire feature stacks from a prompt | *"A FastAPI + Jinja2 OTP login page with 6 individual input boxes, teal #0D9488"* |
| 11 | **Tabnine** | IDE autocomplete | Privacy-focused alternative to Copilot. Can run models locally. | Good for security-conscious teams. Learns your codebase patterns over time. |
| 12 | **Codeium** | IDE autocomplete | Free alternative to GitHub Copilot | Good fallback if Copilot budget is limited. Same quality for Python/Tailwind. |
| 13 | **Perplexity AI** | Research | Latest library versions, package health, recent CVEs | *"What is the latest stable version of Anime.js?"* *"Are there CVEs in PyOTP 2.9?"* |

### Which AI Agent for Which Task

| Task in QuickBite | Best Agent(s) |
|------------------|-|
| Write FastAPI route + Pydantic schema | Cursor (whole file) · Claude (review) · Copilot (autocomplete) |
| Implement OTP cryptographic logic | **Claude first** — security reasoning critical. Then Cursor to write the code. |
| Write SQLAlchemy async model with PostGIS | Cursor · Copilot (column types) |
| PostgreSQL RLS policy SQL | **Claude** — explains security implications. Aider — applies SQL in migration files. |
| Generate OTP login / loyalty card screen | **Stitch.ai** (primary). v0.dev for complex interactive components. |
| Write Celery background task | Cursor · Copilot |
| Implement Anime.js animation timeline | Cursor · Claude — both understand Anime.js timelines |
| Add Animate.css class to a screen | Stitch.ai (add to prompt) or Copilot inline in HTML file |
| Debug a PostgreSQL query / RLS issue | **Claude** — paste the SQL + schema |
| Write pytest integration test | Cursor · Aider |
| Security audit of auth code | **Claude + Security Dev (human)** — never rely on AI alone |
| Full ticket implementation end-to-end | **Devin · Aider** — good for INFRA-01/02/03 and simpler tickets |
| Research: is library X still maintained? | **Perplexity AI** — real-time web search |

---

## Section 2. Software & Coding IDE Stack

### Primary IDEs — Choose One

| IDE | Best For | Price | QuickBite Setup |
|-----|---------|-------|-----------------|
| **Cursor** | AI-native. Full codebase awareness. `Cmd+K` to edit, `Cmd+L` to chat. | Free / $20/mo | Open `quickbite/` folder. Add Doc 2 as `.cursorrules`. Use `@` for doc sections as context. |
| **VS Code** | Best plugin ecosystem. Required if using Copilot. | Free | Install extensions below. Use devcontainer.json for consistent environment. |
| **Windsurf** | Free Cursor alternative. Cascade agent for multi-file changes. | Free | Same setup as Cursor. |
| **PyCharm Professional** | Best Python debugging. Built-in DB browser. PostGIS query console. | $249/yr (free for students) | Use integrated DB tool for PostgreSQL + PostGIS queries. |

### Required VS Code Extensions

| Extension | Category | Why |
|-----------|---------|-----|
| `ms-python.python` | Language | Syntax highlighting, import resolution, FastAPI debugger |
| `ms-python.pylance` | Type checking | Real-time type checking for SQLAlchemy + Pydantic |
| `charliermarsh.ruff` | Linting | Fast Python linter + formatter. Auto-fix on save. |
| `ms-azuretools.vscode-docker` | DevOps | Manage containers, view logs, exec into containers |
| `GitHub.copilot` | AI | Inline autocomplete for Python, Jinja2, Tailwind, SQL |
| `eamodio.gitlens` | Git | Inline git blame, PR review, branch visualisation |
| `rangav.vscode-thunder-client` | API testing | Test FastAPI endpoints directly from VS Code |
| `bradlc.vscode-tailwindcss` | Frontend | Autocomplete Tailwind classes in Jinja2 HTML templates |
| `wholroyd.jinja` | Frontend | Syntax highlighting for `.html` Jinja2 templates |
| `ckolkman.vscode-postgres` | Database | Query PostgreSQL directly from VS Code |
| `Gruntfuggly.todo-tree` | Productivity | Highlights TODO / FIXME / SECURITY comments |
| `usernamehw.errorlens` | Debugging | Inline error display — see Python errors without hovering |

### Supporting Tools

| Tool | Category | Purpose in QuickBite |
|------|---------|----------------------|
| **TablePlus** | DB GUI | Browse PostgreSQL tables, test RLS queries, view encrypted columns, PostGIS geometry viewer |
| **DBeaver** (free) | DB GUI | Free TablePlus alternative. Supports PostGIS geometry viewer. Cross-platform. |
| **Bruno** | API testing | Open-source Postman alternative. Store test requests in git repo under `tests/` folder. |
| **Postman** | API testing | Test all FastAPI endpoints, manage auth headers, environment variables |
| **Docker Desktop** | Containers | Run full QuickBite local stack (FastAPI + PG + Redis + Celery) with one click |
| **GitHub Desktop** | Git GUI | Visual branch management, PR creation, conflict resolution |
| **Linear / Jira** | Project mgmt | Track tickets from Doc 5 (INFRA-01, AUTH-02, etc.) on a Kanban board |
| **Slack** | Communication | GitHub PR notifications, CI status, Sentry error alerts in channels |
| **Notion** | Documentation | Paste Markdown files from this project. Team wiki for decisions. |
| **Loom** | Progress reports | Record 3-minute screen share videos for async progress updates |
| **ngrok** | Webhooks | Expose local FastAPI for Stripe + Twilio webhook testing |
| **mkcert** | Local HTTPS | Trusted SSL certs for localhost — required for OAuth flow testing |

---

## Section 3. Individual Development — Feature Branch Workflow

> **Rule: NEVER commit directly to `main`. Every change goes through: feature branch → local tests → PR → CI → code review → merge. `main` is always deployable.**

### Git Branch Strategy

| Branch | Purpose + Rules |
|--------|----------------|
| `main` | Production-ready code only. Protected. Direct pushes blocked. Deployed to production only after Security Dev sign-off. |
| `develop` | Integration branch. All feature branches merge here first. Deployed to staging automatically. CI must pass. |
| `feature/{ticket-id}` | One branch per ticket. Examples: `feature/auth-01-registration`, `feature/new-otp-01-request`, `feature/loyalty-03-geofence` |
| `fix/{ticket-id}` | Bug fix branches. Example: `fix/otp-rate-limit-counter-bug` |
| `security/{sec-id}` | Security Developer's branches. Example: `security/sec-05-otp-crypto-audit` |
| `release/v1.0` | Release preparation. Branched from develop. Only bug fixes. Merged to main + develop after sign-off. |

### Individual Developer Daily Flow

| Time | Action | Detail |
|------|--------|--------|
| 08:30 | Pull latest develop | `git checkout develop && git pull origin develop` — start fresh every morning |
| 08:35 | Checkout feature branch | `git checkout -b feature/auth-01-registration` (first day) or `git checkout feature/auth-01-registration` |
| 08:40 | Start local Docker stack | `docker-compose up -d` — FastAPI with `--reload` for hot reload |
| 08:45 | Develop the feature | Write code, run tests, iterate. AI agents help here. |
| All day | Run only your tests | `pytest tests/unit/test_customer_otp.py -v` — fast and isolated |
| 17:30 | Commit WIP | `git add -p && git commit -m "feat(otp): phone lookup + Redis store"` |
| 17:45 | Open Draft PR | Draft PR against `develop`. Not ready for review yet but backed up and visible to team. |
| On completion | Full test suite | `pytest --cov=app` — must pass before marking PR as Ready for Review |
| On completion | Open PR for review | Mark Draft PR Ready. Add ticket ID + acceptance criteria status. Request reviewers. |
| After approval | Merge and delete branch | PM merges via GitHub. Delete the feature branch. Start next ticket. |

### Local Isolated Test Environment

- Each developer has their own `.env.local` file (gitignored). Contains local DB URL, local Redis, test API keys.
- `docker-compose up -d` starts: PostgreSQL on 5432, Redis on 6379, FastAPI with `--reload` on 8000, Celery worker.
- Database seeded from `scripts/seed_roles.py` + `scripts/seed_plans.py`. Takes < 30 seconds.
- `pytest` uses a **separate test database** (`quickbite_test`). Torn down after each run. Never touches dev DB.
- Each test uses `@pytest.fixture` with transaction rollback — tests are completely isolated.
- External services (Twilio, SendGrid, Stripe, OpenAI) are **MOCKED** in unit/integration tests. No real calls.
- For webhook testing: `ngrok http 8000` → use the ngrok URL in Stripe + Twilio dashboards.

### Test Ownership

| Developer | Writes These Test Files | What Gets Mocked |
|-----------|------------------------|-----------------|
| Backend Dev 1 | `test_auth.py`, `test_customer_otp.py`, `test_ai_engine.py`, `test_gmb_service.py`, `test_billing.py` | Twilio, SendGrid, OpenAI, Gemini, Stripe, all HTTP |
| Backend Dev 2 | `test_geofence.py`, `test_loyalty.py`, `test_rls.py`, `test_usage_tracking.py`, `test_celery_tasks.py` | PostGIS + Redis = real local. External HTTP mocked. |
| Frontend Dev | Playwright E2E tests for key flows | Uses real local FastAPI. No mocking. |
| Security Dev | `test_otp_brute_force.py`, `test_jwt_attacks.py`, `test_rls_isolation.py`, `test_enumeration.py` | Real local stack. Security tests must test real behaviour. |
| QA Engineer | `test_full_otp_flow.py`, `test_loyalty_flow.py`, `test_review_flow.py` | Mocks external APIs. Tests full HTTP request → response via TestClient. |

### Git Commands — Daily Reference

```bash
# Create your feature branch
git checkout -b feature/new-otp-01-request

# Stage changes interactively (review before committing)
git add -p

# Commit with conventional commit message
git commit -m "feat(otp): add phone OTP request endpoint with rate limiting"

# Push to remote
git push origin feature/new-otp-01-request

# Sync latest develop into your branch (resolve conflicts locally)
git fetch origin && git rebase origin/develop

# Run only your feature's tests (fast)
pytest tests/unit/test_customer_otp.py -v --tb=short

# Full coverage report before PR
pytest --cov=app --cov-report=html

# Lint check (must pass before PR)
ruff check app/

# Fresh start (tear down local stack + volumes)
docker-compose down -v
```

---

## Section 4. Detailed Task Breakdown — Hour Estimates

> **Note:** Hours are for an experienced developer. Add 50% for AI-assisted junior devs. Deduct 30% if Cursor + Claude are used throughout.

### Weeks 1–2: Foundation

| Ticket | Feature | Sub-tasks | Est (hrs) | Owner | AI Help |
|--------|---------|-----------|:---------:|-------|---------|
| INFRA-01 | Project Skeleton | Folder structure, requirements, config.py, health endpoint, ruff | 4 | All | Cursor |
| INFRA-02 | PostgreSQL + PostGIS | SQLAlchemy async, base model, PostGIS, first migration | 6 | Back2 | Cursor + Claude |
| INFRA-03 | Redis Setup | Cache service, Celery broker, connection pool | 4 | Back2 | Cursor |
| Docker | Local Stack | docker-compose, devcontainer.json, Makefile | 5 | DevOps | Copilot |
| CI | GitHub Actions | ci.yml: ruff, bandit, pytest, trivy, staging deploy | 4 | DevOps | Copilot |
| SEC-01 | Threat Model | Document all attack surfaces and mitigations | 6 | Sec Dev | Claude |
| SEC-02 | CI Security Tools | bandit, trivy, safety, git-secrets in CI | 4 | Sec Dev | — |
| AUTH-01 | Registration | bcrypt hash, verify email, tenant provision, RLS | 8 | Back1 | Cursor + Copilot |
| AUTH-02 | Login + TOTP MFA | Two-step login, MFA session, TOTP verify, JWT, rate limits | 8 | Back1 | Cursor + Claude |
| AUTH-03 | JWT Refresh | Single-use rotation, jti Redis revocation, logout-all | 5 | Back1 | Cursor |
| AUTH-04 | RBAC Guards | `get_current_user()`, `require_role()`, `check_subscription_tier()` | 5 | Back2 | Cursor |
| TENANT-01 | Subdomain Middleware | ASGI Host parse, Redis tenant lookup, request.state | 4 | Back2 | Claude |
| TENANT-02 | RLS Policies | 15 table RLS enables + policies, SQLAlchemy event listener | 6 | Back2 | Claude |
| SEC-03 | Auth Security Audit | Verify bcrypt w12, JWT HS256 only, 256-bit key, TOTP RFC 6238 | 4 | Sec Dev | Claude |
| SEC-04 | RLS Audit | 10 cross-tenant SQL queries. Verify 0 rows leak. | 3 | Sec Dev | — |
| Stitch.ai | Auth Screens | Login, MFA setup, registration, email verification | 6 | Front | Stitch.ai |

### Week 3: OTP + Billing ★ Critical Security Week

| Ticket | Feature | Sub-tasks | Est (hrs) | Owner | AI Help |
|--------|---------|-----------|:---------:|-------|---------|
| SUB-01 | Stripe Plans | Seed 3 plans, Checkout sessions, webhook handler | 8 | Back1 | Cursor + Claude |
| SUB-02 | Usage Tracking | usage_tracking ops, check_plan_limit(), 80/100% gates | 5 | Back2 | Cursor |
| SUB-03 | Upgrade Flow | Upgrade modal, checkout redirect, plan refresh | 5 | Back1 | Cursor |
| NEW-OTP-01 | OTP Request Endpoint | Phone + email, Redis store, Twilio SMS, rate limit | 6 | Back1 | Cursor + Claude |
| NEW-OTP-02 | OTP Verify + JWT | SHA-256 compare, 3-attempt limit, customer JWT (7-day) | 5 | Back1 | Cursor |
| NEW-OTP-03 | Customer Registration | AES-256-GCM PII encrypt, WhatsApp opt-in, first stamp link | 6 | Back2 | Cursor + Claude |
| **SEC-05** | **OTP Crypto Audit** | **Grep for `random.randint`. Verify `secrets.token_digits(6)`. Redis TTL = 300.** | **2** | **Sec Dev** | **—** |
| **SEC-06** | **Enumeration Prevention** | **Test: unknown phone → assert status_code == 200 + `{"status":"new_user"}`** | **2** | **Sec Dev** | **—** |
| **SEC-07** | **Rate Limit Tests** | **Automated: 3 wrong codes → OTP deleted from Redis. 2-min cooldown blocks.** | **3** | **Sec Dev** | **Claude** |
| **SEC-08** | **JWT Key Isolation** | **Customer token rejected on owner route. Owner token rejected on customer route.** | **2** | **Sec Dev** | **—** |
| **SEC-09** | **PII Encryption Audit** | **SQL: no raw phone/email visible. AES-256-GCM IV randomness verified.** | **3** | **Sec Dev** | **Claude** |
| **SEC-10** | **Stripe Webhook** | **Invalid sig → assert 401, no DB change.** | **2** | **Sec Dev** | **—** |
| Stitch.ai | OTP Screens | OTP login (6-box), customer registration, stamp collected | 5 | Front | Stitch.ai |

### Weeks 4–6: Core Features

| Ticket | Feature | Est (hrs) | Owner | AI Help |
|--------|---------|:---------:|-------|---------|
| REVIEW-01 | Review Composer | 8 | Back1 | Cursor + Claude |
| REVIEW-02 | AI Response Drafting | 7 | Back1 | Cursor |
| REVIEW-03 | GMB OAuth + Sync | 8 | Back1 | Cursor + Claude |
| LOYALTY-01 | Branch + GPS | 5 | Back2 | Cursor |
| LOYALTY-02 | QR Code Generation | 4 | Back2 | Copilot |
| LOYALTY-03 | Geofence + Stamp Log | 7 | Back2 | Cursor + Claude |
| LOYALTY-04 | Rewards + Redemption | 6 | Back2 | Cursor |
| DASH-01 | Dashboard + WebSocket | 7 | Back1+2 | Cursor |
| DASH-02 | Loyalty Analytics | 5 | Back2 | Cursor |
| SEC-11 to SEC-16 | Security audit tasks (PKCE, prompt injection, geofence fraud, QR token, Celery idempotency, WebSocket) | 12 | Sec Dev | Claude / — |
| Stitch.ai | Customer + Dashboard Screens | 16 | Front | Stitch.ai + Anime.js |

### Weeks 7–8: Pen Testing + Polish

| Ticket | Feature | Est (hrs) | Owner |
|--------|---------|:---------:|-------|
| ADMIN-01 | Super Admin Panel | 6 | Back1 |
| SEC-17 to SEC-24 | Full pen test plan + auth bypass + OTP attacks + injection + OWASP ZAP + Burp Suite + RLS final + security report | 37 | Sec Dev |
| Load testing | Locust 100 concurrent users | 4 | DevOps |
| Bug fixes | All QA + pen test issues | 20 | All |

### Hour Totals by Role

| Role | Total Hours | Notes |
|------|:-----------:|-------|
| Backend Dev 1 | ~180 hrs | Auth, AI, GMB, OTP, Billing, Admin, WebSocket |
| Backend Dev 2 | ~160 hrs | DB schema, RLS, Geofence, Loyalty, Usage, Celery |
| Frontend Dev | ~120 hrs | All screens via Stitch.ai + animations |
| DevOps Engineer | ~80 hrs | CI setup, Docker, staging, production, monitoring |
| **Security Developer** | **~100 hrs** | **30 SEC tasks — peaks in weeks 7–8** |
| QA Engineer | ~80 hrs | Integration tests, load tests, pre-launch checklist |
| Product Manager | ~60 hrs | Sprint planning, pilot onboarding, metrics |
| **TOTAL** | **~780 hrs** | **~65 hrs/week total team capacity** |

---

## Section 5. Progress Reports & Communication

### 5.1 Daily Standup Format (post in Slack #standup by 9am)

```
Yesterday:  Completed AUTH-01 (registration endpoint). All 12 tests passing. PR #24 open for review.
Today:      Starting AUTH-02 (Login + TOTP MFA). Branch: feature/auth-02-login. Estimate: 8 hours.
Blockers:   None.
```

```
Yesterday:  SEC-05 OTP crypto audit PASSED. secrets.token_digits(6) confirmed. SEC-06 PASSED.
Today:      SEC-07 rate limit verification — writing automated test script.
Blockers:   Need ngrok URL for Twilio webhook test — DevOps please share.
```

### 5.2 Pull Request Description Template

```markdown
## Ticket
Closes #NEW-OTP-01 — Customer OTP Request Endpoint (Phone + Email)

## What Changed
- Added POST /auth/customer/otp-request (phone variant)
- Added POST /auth/customer/otp-request (email variant)
- OTP generated via secrets.token_digits(6) — NOT random.randint()
- SHA-256(otp) stored in Redis with 300-second TTL
- Rate limit: 1 request per phone per 2 minutes via SlowAPI

## Acceptance Criteria Status
- [x] Valid phone → OTP SMS delivered within 10 seconds
- [x] Unknown phone → returns {"status":"new_user"} (NOT 404)
- [x] 2nd request within 2 min → 429 with Retry-After header
- [x] 6th request in 24 hours → 429 "Daily limit reached"
- [x] OTP stored as SHA-256(otp) (not plaintext) — confirmed in Redis
- [x] Redis key format: otp:{tenant_id}:{SHA-256(phone)}

## How to Test Locally
1. docker-compose up -d
2. pytest tests/unit/test_customer_otp.py -v
3. Manual: curl -X POST http://localhost:8000/api/v1/auth/customer/otp-request \
   -H "Content-Type: application/json" \
   -d '{"phone":"+919876543210"}'

## Notes for Reviewer
- Email variant in same file, separate function
- Twilio mocked in tests — no real SMS sent in test environment

## Reviewer(s)
- Code: Backend Dev 2 (cross-review)
- 🔐 Security review: Security Dev REQUIRED (auth endpoint)
```

### 5.3 Weekly Progress Report (post in Slack #weekly-progress every Friday 5pm)

```
### Weekly Progress Report — Week 3 (OTP + Billing Week)
### [Your Name] — Backend Dev 1

**Completed this week:**
- NEW-OTP-01: Customer OTP request endpoint — PR #41 MERGED
- NEW-OTP-02: OTP verify + customer JWT — PR #43 MERGED
- SEC-05 PASSED by Security Dev: OTP crypto audit confirmed clean
- SUB-01: Stripe plans + checkout sessions — PR #45 IN REVIEW

**In progress:**
- SUB-03: Upgrade flow + billing portal (50% done, finishing Monday)

**Blocked:** Nothing currently blocked.

**Numbers:**
- Tickets completed: 3 of 4 planned
- Tests written: 28 new tests (all passing)
- PRs merged: 2  |  PRs in review: 1

**Next week:**
- REVIEW-01 (AI review composer endpoint)
- REVIEW-03 (GMB OAuth + sync)
```

### 5.4 Security Findings Report (Security Dev — after Weeks 7–8)

```
### QuickBite Security Findings Report — Week 8
### Security Dev Sign-Off Status

Summary:
- Critical: 0
- High: 1 (FIXED)
- Medium: 3 (FIXED)
- Low: 5 (2 fixed, 3 accepted)

Finding: HIGH — SEC-19-001
Title:      OTP brute-force possible without per-IP rate limit
Endpoint:   POST /auth/customer/otp-verify
Evidence:   100 OTP attempts from 3 IPs. 3-attempt limit only per-phone, not per-IP.
Remediation: Added per-IP counter in Redis (5 verify attempts/hour/IP). PR #87.
Status:     FIXED — re-tested 2025-01-16 — CONFIRMED FIXED

Pre-Launch Checklist: 17/17 items PASSED
Signed off: [Security Developer Name]
Date: [Date]
```

### 5.5 Loom Progress Video (Optional — Async Demo)
- Record 2–3 minute Loom at end of each major feature. Share link in Slack.
- Contents: (1) show feature working in browser, (2) show key test passing, (3) show PR description.
- Security Developer records a video for every SEC task showing evidence (test output, Burp screenshots, ZAP report).

---

## Section 6. All Latest Files

### Word Documents (.docx) — Latest Versions

| File Name | Version | Size | Contents |
|-----------|---------|------|---------|
| `Doc1_PRD_v2.docx` | v2.0 | 32 KB | PRD with OTP auth, seamless registration, subscription tiers, all app flows, success metrics |
| `Doc2_Technical_Architecture_v2.docx` | v2.0 | 45 KB | 37 technologies including Anime.js + Animate.css. 15 DB tables. All env variables. |
| `Doc3_Security_Access_v2.docx` | v2.0 | 32 KB | 3 auth flows, 6-role RBAC, RLS on 15 tables, 40+ error scenarios, 35+ edge cases |
| `Doc4_Frontend_Spec_v3.docx` | **v3.0 ★ LATEST** | 31 KB | 4-library animation stack. Decision matrix. CDN loading order. Anime.js + Animate.css code examples. |
| `Doc5_Feature_Tickets.docx` | v1.0 | 28 KB | 25 tickets with acceptance criteria, dependencies, priority labels |
| `Doc6_SysArch_DevTasks_TeamPlan_v1.1.docx` | **v1.1 ★ LATEST** | 35 KB | Sprint plan with Security Developer (SEC-01 to SEC-30). RACI matrix. 17-point checklist. |
| `QuickBite_Tech_Study_Notes.docx` | **v3.0 ★ LATEST** | 45 KB | Study notes for all 37 technologies including Anime.js + Animate.css |
| `DevGuide_AI_Agents_IDE_Workflow.docx` | **v1.0 ★ NEW** | — | This document — AI agents, IDEs, branch workflow, hour estimates, progress reports |

### Markdown Files (.md) — Latest Versions

| File Name | Version | Contents |
|-----------|---------|---------|
| `00_Project_Overview.md` | v1.0 | Simple one-pager |
| `01_PRD.md` | v2.0 | Full PRD in Markdown |
| `02_Technical_Architecture.md` | v2.0 | Full tech architecture including Anime.js + Animate.css |
| `03_Security_Access.md` | v2.0 | Full security document |
| `04_Frontend_Spec.md` | **v3.0 ★ LATEST** | 4-library animation stack with decision matrix + code examples |
| `05_Feature_Tickets.md` | v1.0 | All tickets as checkboxes — copy into AI agents |
| `06_SysArch_TeamPlan.md` | **v1.1 ★ LATEST** | Sprint plan with Security Developer + RACI matrix |
| `07_Tech_Study_Notes.md` | **v3.0 ★ LATEST** | All 37 technology study notes including Anime.js + Animate.css |
| `08_Dev_Guide.md` | **v1.0 ★ NEW** | This document |

### Document Version History

| Document | Current | Changed From | What Changed |
|---------|---------|-------------|-------------|
| PRD | v2.0 | v1.0 | Customer OTP auth, seamless registration, Stitch.ai feature, updated metrics |
| Technical Architecture | v2.0 | v1.0 | Stitch.ai, Anime.js 3.2.1, Animate.css 4.1.1, customers table, OTP env vars |
| Security & Access | v2.0 | v1.0 | Customer OTP auth flow, registration flow, customer permission matrix, OTP errors |
| **Frontend Spec** | **v3.0** | v2.0 | **4-library animation stack, decision matrix, CDN order, Anime.js + Animate.css code** |
| Feature Tickets | v1.0 | — | Original. Includes NEW-OTP-01/02/03 for customer OTP auth. |
| **System Arch + Team Plan** | **v1.1** | v1.0 | **Security Developer as 7th team member. 30 SEC tasks. RACI updated. Phase gates.** |
| **Tech Study Notes** | **v3.0** | v2.0 | **Added F-06 Anime.js and F-07 Animate.css. Total technologies: 37.** |
| **Dev Guide** | **v1.0** | NEW | **AI agents, IDEs, branch workflow, hour estimates, progress reports** |

---

*QuickBite AI + Loyalty · Developer & AI Agent Working Guide · v1.0 · Confidential*
