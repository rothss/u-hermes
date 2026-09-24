$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

if (-not (Test-Path ".venv")) {
    py -3.13 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. Fill DASHSCOPE_API_KEY and HERMES_API_KEY before running." -ForegroundColor Yellow
}
Write-Host "Bootstrap complete." -ForegroundColor Green
