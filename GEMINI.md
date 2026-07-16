# QuickBite AI + Loyalty — Antigravity Rules
# File: GEMINI.md (project root) — read by Antigravity agents on every session
# Supplements: AGENTS.md (shared rules — read that first)

## Read First
@AGENTS.md

---

## Antigravity-Specific Configuration

### Model Recommendations for QuickBite Tasks
| Task Type | Recommended Model | Why |
|-----------|------------------|-----|
| Security-sensitive code (OTP, JWT, RLS, encryption) | Claude Sonnet 4.6 | Superior security reasoning, better on SWE-bench |
| Complex multi-file backend features (REVIEW-01, LOYALTY-03) | Claude Sonnet 4.6 | Better Pydantic v2 + SQLAlchemy 2.x pattern knowledge |
| Boilerplate generation (INFRA-01/02/03, DOCKER) | Gemini 3 Pro | Speed + large context window for reading all docs |
| Research tasks (library versions, CVEs, PostGIS syntax) | Gemini 3 Pro | Real-time web search, large context |
| Stitch.ai screen wiring (Jinja2 template work) | Gemini 3 Pro or Claude | Fast, simple substitution tasks |
| Animation work (Anime.js timelines, ANIM tickets) | Claude Sonnet 4.6 | Better JavaScript reasoning |
| SEC tickets (pen testing, security audit writing) | Claude Sonnet 4.6 | Security specialisation |
| Simple formatting, naming, boilerplate | Haiku 4.5 | Cost-efficient for trivial tasks |

### Parallel Agent Strategy (Manager View)
Use Antigravity's Manager view to run multiple agents simultaneously:

**Typical parallel setup for a feature week:**
- Agent 1 → Backend (Auth/OTP/Loyalty service implementation)
- Agent 2 → Schemas + Tests (Pydantic schemas + pytest tests)
- Agent 3 → Security Dev (SEC ticket audit for this week's features)
- Agent 4 → Frontend (Stitch.ai screen generation + wiring)

Never run two agents editing the same file simultaneously — assign file ownership per agent.

---

## Antigravity Rules Folder
Rules live in `.agent/rules/`. Use `model_decision` for most rules (avoids context overload).
Only mark rules `always_on` if the agent MUST know them regardless of task type.

---

## Allow List / Deny List for Agent Commands

### ✅ ALLOW without asking
```
pytest ...                         # running tests
ruff check app/                    # linting
ruff format app/                   # formatting
alembic upgrade head               # applying migrations
alembic revision --autogenerate ... # generating migrations
docker-compose up -d               # starting local stack
docker-compose down                # stopping local stack
python scripts/seed_*.py           # seeding dev data
pip install ... --dry-run          # checking packages
cat ... / head ... / grep ...      # reading files
ls ...                             # directory listing
git status / git diff / git log    # git inspection
```

### ⚠️ ASK before executing
```
git add ... / git commit ...       # modifying git history
git push ...                       # pushing to remote
docker-compose down -v             # destroys volumes (data loss)
pip install ...                    # installing new packages
alembic downgrade ...              # reverting migrations
DROP TABLE ... / DELETE FROM ...   # destructive DB operations
```

### ❌ DENY always
```
git push --force ...               # force push — never
rm -rf ...                         # destructive file operations
curl ... | bash                    # remote code execution
python -c "import os; os.system(...)"  # shell injection
any command involving production environment variables
```

---

## Antigravity Artifacts

Every time an agent completes a major task, request an Artifact:
- **Implementation plan** before coding (what files, what changes)
- **Test results screenshot** after running pytest
- **Browser screenshot** after Stitch.ai screen is wired (if applicable)
- **Security finding** after any SEC ticket audit

Review Artifacts before approving the next step. This is your primary oversight mechanism.

---

## Context Files to Include at Session Start

When starting a session in Antigravity, include these in the context:
1. `AGENTS.md` — shared rules (always)
2. `app/core/config.py` — env vars and settings (always)
3. The specific service file for the feature you're building
4. The relevant test file (or create it)
5. The Doc 5 ticket acceptance criteria for the feature

For database work, also include:
- `app/db/models/` folder (for related models)
- `db/migrations/versions/` (latest migration file)

---

## Antigravity-Specific Skills

Skills are in `.agent/skills/`. Antigravity will load them when the task matches.
Available skills for QuickBite (see `.agent/skills/` folder):
- `fastapi-route` — how to write a FastAPI route for QuickBite
- `sqlalchemy-async` — SQLAlchemy 2.x async patterns for QuickBite models
- `celery-idempotent` — how to write an idempotent Celery task
- `customer-otp-flow` — the complete OTP flow with security constraints
- `stitch-screen` — how to generate + wire a Stitch.ai screen
- `anime-animation` — Anime.js patterns for stamp, reward, and OTP animations
- `postgis-geofence` — ST_DWithin geofence query pattern
- `security-audit` — how to conduct and document a SEC ticket audit
- `pytest-pattern` — QuickBite test structure with mocking patterns

---

## Workflows

Workflows are in `.agent/workflows/`. Trigger with slash commands:
- `/run-tests` — run pytest with coverage
- `/security-scan` — bandit + trivy + safety check
- `/dev-startup` — docker-compose + migrations + seed data
- `/pre-pr-check` — ruff + bandit + full pytest suite (run before every PR)
- `/stitch-screen` — Stitch.ai generation + export + wiring sequence
