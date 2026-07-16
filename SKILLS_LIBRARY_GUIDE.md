# QuickBite AI + Loyalty
## Skills Library Guide — Which Community Skills to Use

> This document maps community skill libraries (Agentpedia, GitHub `antigravity-skills` topic)
> to QuickBite tickets, and explains when to use built-in vs custom skills.

---

## How Skills Work

**Antigravity:** Skills live in `.agent/skills/{skill-name}/SKILL.md`. The agent loads the right
skill file when the task description matches the skill's `description` or `tags` frontmatter.

**Claude Code (in Claude.ai):** Claude has a built-in `/mnt/skills/public/` library.
Check it with `ls /mnt/skills/public/` — read `SKILL.md` before creating any file.

---

## Built-In Claude Code Skills (use these first)

These are baked into the Claude Code environment. Always `view` the SKILL.md before creating any output file.

| Skill | Path | Use For |
|-------|------|---------|
| **docx** | `/mnt/skills/public/docx/SKILL.md` | Creating/updating any `.docx` documentation file |
| **pdf** | `/mnt/skills/public/pdf/SKILL.md` | Creating or filling PDF files (QR download PDFs) |
| **pptx** | `/mnt/skills/public/pptx/SKILL.md` | Presentation decks for stakeholders |
| **xlsx** | `/mnt/skills/public/xlsx/SKILL.md` | Analytics export spreadsheets (NICE-04) |
| **frontend-design** | `/mnt/skills/public/frontend-design/SKILL.md` | Any UI generation — use BEFORE writing Stitch.ai prompts |
| **file-reading** | `/mnt/skills/public/file-reading/SKILL.md` | Reading uploaded files (PDFs, docs) |
| **data-analysis** | `/mnt/skills/public/data-analysis/SKILL.md` | Loyalty analytics, review sentiment analysis |

---

## Community Skills — Agentpedia.codes (500+ Skills)

Browse: **https://agentpedia.codes/** — search by technology name.

### Python / FastAPI Skills (use for backend tickets)
| Community Skill | Use For | QuickBite Tickets |
|-----------------|---------|------------------|
| `fastapi-python` | FastAPI route patterns, Pydantic v2, dependency injection | AUTH-01 to LOYALTY-04 |
| `sqlalchemy-async` | SQLAlchemy 2.x async, session management, type annotations | INFRA-02, all model work |
| `python-celery` | Celery task patterns, retry logic, beat schedules | REVIEW-02, LOYALTY-04, NICE-03 |
| `python-jwt` | PyJWT encode/decode, token validation, refresh patterns | AUTH-02, AUTH-03 |
| `python-testing` | pytest fixtures, async testing, factory-boy patterns | All test files |
| `python-security` | bandit rules, secrets management, bcrypt patterns | SEC-01 to SEC-30 |
| `postgresql-postgis` | PostGIS queries, GeoAlchemy2, spatial indexes | LOYALTY-01, LOYALTY-03 |
| `redis-python` | Redis patterns, rate limiting, TTL management | INFRA-03, NEW-OTP-01 |
| `stripe-python` | Stripe webhooks, checkout sessions, subscription events | SUB-01, SUB-02, SUB-03 |
| `openai-python` | GPT-4o API, chat completions, error handling | REVIEW-01 |

### Frontend Skills (use for STITCH and ANIM tickets)
| Community Skill | Use For | QuickBite Tickets |
|-----------------|---------|------------------|
| `tailwindcss` | Tailwind utility classes, responsive design, custom colours | All STITCH tickets |
| `jinja2` | Jinja2 template syntax, filters, template inheritance | All STITCH tickets (wiring) |
| `anime-js` | Anime.js timelines, stagger, spring easing, SVG | ANIM-01 to ANIM-04 |
| `three-js` | Three.js scenes, materials, camera, render loop | ANIM-05 |
| `gsap` | GSAP tweens, ScrollTrigger, timelines | ANIM-05 |
| `animate-css` | Animate.css classes, duration override, re-triggering | ANIM-06 |
| `chart-js` | Chart.js config, datasets, responsive options | DASH-01, DASH-02 |

### DevOps Skills (use for CI and Docker)
| Community Skill | Use For | QuickBite Tickets |
|-----------------|---------|------------------|
| `docker-compose` | docker-compose.yml, volumes, networking, health checks | DOCKER ticket |
| `github-actions` | CI/CD workflows, job steps, secrets, matrix builds | CI ticket |
| `alembic` | Migration files, autogenerate, upgrade/downgrade, PostGIS | INFRA-02, all model changes |
| `sentry-python` | Error tracking setup, PII scrubbing, alert rules | SEC-29 |

---

## Custom Skills in `.agent/skills/` (QuickBite-specific)

These are in your repo's `.agent/skills/` folder — project-specific knowledge no community skill has.

| Custom Skill | File | What It Knows |
|-------------|------|--------------|
| `customer-otp-flow` | `customer-otp-flow.md` | Complete OTP auth flow, Redis key patterns, security constraints, test structure |
| `fastapi-route` | `fastapi-route.md` | QuickBite route pattern (thin handler + service layer), error codes, rate limiting |
| `celery-idempotent` | `celery-idempotent.md` | Idempotency key pattern for Twilio/GMB/email tasks, retry strategy |
| `anime-animation` | `anime-animation.md` | Anime.js patterns for stamp counter, reward timeline, OTP stagger, star cascade |
| `security-audit` | `security-audit.md` | SEC ticket audit framework, code review patterns, findings documentation |
| `stitch-screen` | `stitch-screen.md` | Stitch.ai prompt writing, Jinja2 wiring, required DOM IDs for JS, export paths |

---

## When to Use Which

### Decision Tree

```
New ticket → What type of file am I creating?
│
├── .docx / .pdf / .xlsx / .pptx
│   └── ✅ Read Claude Code built-in skill FIRST (/mnt/skills/public/{type}/SKILL.md)
│
├── FastAPI route / SQLAlchemy model / Pydantic schema
│   ├── ✅ Read .agent/skills/fastapi-route.md (custom — QuickBite specific)
│   └── ✅ Search agentpedia.codes for 'fastapi-python' or 'sqlalchemy-async'
│
├── OTP / Auth / JWT code
│   ├── ✅ Read .agent/skills/customer-otp-flow.md (custom — has our specific patterns)
│   └── ✅ .agent/rules/02-security.md is always_on — already loaded
│
├── Celery task
│   └── ✅ Read .agent/skills/celery-idempotent.md (custom — idempotency pattern)
│
├── Stitch.ai screen (STITCH-XX ticket)
│   ├── ✅ Read .agent/skills/stitch-screen.md (custom — our specific workflow)
│   └── ✅ Search agentpedia.codes for 'tailwindcss' + 'jinja2'
│
├── Animation (ANIM-XX ticket)
│   ├── ✅ Read .agent/skills/anime-animation.md (custom — our specific patterns)
│   └── ✅ Search agentpedia.codes for 'anime-js' (for additional Anime.js patterns)
│
├── Security audit (SEC-XX ticket)
│   └── ✅ Read .agent/skills/security-audit.md (custom — our audit framework)
│
└── PostGIS / geofence
    ├── ✅ Read .agent/rules/03-database.md (already has the pattern)
    └── ✅ Search agentpedia.codes for 'postgresql-postgis'
```

---

## Skills Loading in Antigravity — Practical Tips

### Explicit skill loading
In Antigravity's agent input, reference the skill directly:
```
"Using the celery-idempotent skill, implement the dispatch_reward_notification task
for LOYALTY-04. The task sends WhatsApp if opt-in, else SMS fallback."
```

### Skill stacking (use multiple)
```
"Using the fastapi-route skill and customer-otp-flow skill, implement NEW-OTP-01.
Use the python-jwt community skill for JWT generation."
```

### In Claude Code (in terminal)
```bash
# Claude Code reads CLAUDE.md automatically
# Mention skills explicitly in your request:
claude "Using the patterns in .agent/skills/anime-animation.md, add the
stamp counter animation to app/templates/customer/loyalty_card.html for ANIM-03."
```

---

## Adding New Custom Skills

When you solve a problem that will recur, create a new skill:
```bash
# Create the skill file
cat > .agent/skills/twilio-whatsapp.md << 'EOF'
---
name: twilio-whatsapp
description: How to send a WhatsApp Business message via Twilio for QuickBite rewards
tags: [twilio, whatsapp, messaging, loyalty]
---
[document the pattern here]
EOF
```

Good candidates for new custom skills:
- GMB OAuth + PKCE token exchange (REVIEW-03)
- PostGIS geofence query (LOYALTY-03)
- AES-256-GCM PII encryption/decryption pattern (NEW-OTP-03)
- Stripe subscription webhook handling (SUB-01)

---

## Skills for Each Sprint Phase

| Phase | Week | Recommended Skills |
|-------|------|--------------------|
| Foundation | 1–2 | `docker-compose`, `alembic`, `python-testing`, `github-actions` |
| OTP + Billing | 3 | `customer-otp-flow` ★, `python-jwt`, `redis-python`, `stripe-python` |
| Core Features | 4–6 | `fastapi-route` ★, `sqlalchemy-async`, `postgresql-postgis`, `openai-python` |
| Frontend Screens | 4–7 | `stitch-screen` ★, `tailwindcss`, `jinja2`, `frontend-design` (built-in) |
| Animations | 6–7 | `anime-animation` ★, `three-js`, `gsap`, `animate-css` |
| Security | All | `security-audit` ★, `python-security`, `celery-idempotent` ★ |
| Pen Testing | 7–8 | `security-audit` ★ (phases 3+4) |
| Monitoring | 11–12 | `sentry-python` |

★ = Custom QuickBite skill — use this before any community skill for these tasks
