---
description: Run full security scan — bandit, trivy, safety, ruff. Run before every PR.
slash_command: /security-scan
---

## Pre-PR Security Scan

1. Python static security analysis (bandit)
   `bandit -r app/ -ll -q`

2. Check for known CVEs in Python dependencies (safety)
   `safety check --short-report`

3. Lint check (ruff)
   `ruff check app/`

4. Check for hardcoded secrets
   `git secrets --scan`

5. Docker image vulnerability scan (trivy) — run if Dockerfile changed
   `docker build -t quickbite:scan . && trivy image --severity HIGH,CRITICAL quickbite:scan`

6. Summary
   If all pass: "✅ Security scan passed — safe to open PR"
   If any fail: List each failure with the file and line number

## What Each Tool Catches
- **bandit**: hardcoded passwords, use of `subprocess.shell=True`, MD5/SHA1 for passwords, `assert` in non-test code, SQL injection patterns
- **safety**: Python packages with known CVEs (checks PyPI advisory database)
- **ruff**: code quality, unused imports, type errors
- **git-secrets**: API keys, passwords, private keys accidentally committed
- **trivy**: CVEs in base Docker image and installed packages
