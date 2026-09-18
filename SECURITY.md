# Security Policy

QuickBite AI + Loyalty is a multi-tenant SaaS platform handling restaurant owner
credentials, staff accounts, and customer PII (phone numbers, emails, GPS scan
history). We take reports of security vulnerabilities seriously and appreciate
the work of independent researchers who help us find and fix them responsibly.

## Reporting a Vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Public
issues are indexed and visible to everyone, including anyone who might exploit
the report before a fix ships.

Report privately through one of these channels, in order of preference:

1. **GitHub Security Advisories** — use the "Report a vulnerability" button
   under this repository's Security tab (private, becomes a draft advisory
   only you and maintainers can see).
2. **Email:** `security@quickbite.ai`

   > **Action required before this policy is fully in force (SEC-26):** this
   > address must be a real, monitored inbox before launch, and GitHub's
   > private vulnerability reporting feature must be enabled on this
   > repository (Settings → Security → Private vulnerability reporting). Both
   > are manual repo/organization-admin actions this document cannot perform —
   > track them as the two remaining SEC-26 acceptance criteria.

When reporting, please include:

- A description of the vulnerability and its potential impact.
- Step-by-step reproduction instructions (a request/response pair, a script,
  or a proof-of-concept payload).
- The affected endpoint, component, or file, if known.
- Whether you have already disclosed it anywhere else.

We do not require you to attempt exploitation beyond what's needed to
demonstrate the issue — please stop at proof of concept and avoid accessing,
modifying, or exfiltrating real tenant or customer data.

## Scope

### In scope

- This repository's source code (`app/`, `docs/`, `scripts/`, Alembic
  migrations, Celery tasks).
- Once deployed, the production and staging QuickBite API and admin panel
  (domains will be added here at first deploy — see SEC-28/SEC-29).
- Authentication and session handling (owner/staff JWT, customer OTP,
  refresh-token rotation, MFA).
- Multi-tenant data isolation (row-level security policies and the
  `app.tenant_id` scoping mechanism).
- Payment webhook handling (Razorpay signature verification and replay
  protection).
- The loyalty QR-scan and geofence flow.

### Out of scope

- **Third-party services we depend on but do not operate**: Twilio, 2Factor.in,
  SendGrid, Razorpay, Google My Business, OpenAI, Google Gemini, Cloudflare R2,
  Supabase's own infrastructure (report vulnerabilities in those platforms
  directly to their vendors).
- Denial-of-service or resource-exhaustion attacks (volumetric DoS, not
  application-logic rate-limit bypass — the latter *is* in scope).
- Social engineering, phishing, or physical attacks against staff, restaurant
  owners, or customers.
- Automated vulnerability scanning against production without prior
  coordination — coordinate with us first so we can distinguish your traffic
  from an actual attack. Authorized internal testing follows the scope defined
  in [`docs/PENTEST_PLAN.md`](docs/PENTEST_PLAN.md); that plan does not extend
  an invitation to external researchers to scan production unannounced.
- Reports that require physical access to a device, a rooted/jailbroken
  device, or a compromised end-user machine as a precondition.
- Missing security headers or best-practice suggestions with no demonstrated
  exploitability (e.g., "no HSTS preload" alone, without a scenario it enables).
- Issues in unbuilt/stubbed features flagged as such in
  [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) (e.g., Google My Business
  OAuth, QR token generation, loyalty anti-fraud beyond the geofence check) —
  these aren't yet live surfaces to attack.

## Our Commitment

- We will acknowledge receipt of a report within **3 business days**.
- We will provide an initial assessment (confirmed / needs more info / not
  applicable) within **10 business days**.
- We aim to remediate confirmed vulnerabilities on this timeline, from
  confirmation:
  - **Critical** (full account takeover, cross-tenant data exposure, remote
    code execution, payment bypass): 7 days
  - **High** (auth bypass on a specific role/flow, significant PII exposure):
    30 days
  - **Medium**: 90 days
  - **Low**: best-effort, may be batched into a normal release
- We will keep you informed of progress and credit you (if you'd like) once a
  fix ships, unless you ask to remain anonymous.

## Safe Harbor

We will not pursue legal action against researchers who:

- Make a good-faith effort to avoid privacy violations, data destruction, and
  service disruption during their research.
- Only interact with accounts and data they own or have explicit permission to
  test.
- Give us a reasonable time to remediate before any public disclosure
  (**90 days** from our acknowledgment, coordinated disclosure — we're happy
  to negotiate this if a fix needs more time and you're kept informed of why).
- Do not exploit a vulnerability beyond what's necessary to prove it exists.

This safe harbor does not extend to third-party systems (see Out of Scope).

## Bug Bounty

We do not currently run a paid bug bounty program. We're happy to publicly
credit researchers in release notes or a security acknowledgments page, with
your permission.

## Coordinated Disclosure

We follow coordinated disclosure: please give us the remediation window above
before publishing details publicly. If we go silent or miss the acknowledgment
window, that itself is fine to mention when you do disclose — we'd rather you
disclose responsibly late than not at all.
