# Local dev runner: DB + Redis in Docker, app on the host venv.
# Usage: .\scripts\run-local.ps1          (first run does migrate + seed)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker not found. Install Docker Desktop (winget install Docker.DockerDesktop), start it, re-run."
}
docker compose up -d db redis
if ($LASTEXITCODE -ne 0) { throw "docker compose failed - is Docker Desktop running?" }

Write-Host "Waiting for Postgres..."
do { Start-Sleep 2; docker compose exec -T db pg_isready -U quickbite -d quickbite | Out-Null } while ($LASTEXITCODE -ne 0)

# Migrations run as the owner (MIGRATION_DATABASE_URL); the app role is created after.
& .\.venv\Scripts\alembic.exe upgrade head
$env:QUICKBITE_APP_DB_PASSWORD = "quickbite"
$env:DATABASE_URL = "postgresql+asyncpg://quickbite:quickbite@localhost:5432/quickbite"
& $py scripts\create_app_role.py
& $py scripts\seed_roles.py
& $py scripts\seed_plans.py
Remove-Item Env:DATABASE_URL

Write-Host "Starting app on http://localhost:8000"
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
