$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir
if (-not (Test-Path "models/Qwen3-4B-Q4_K_M.gguf")) { throw "Сначала выполните scripts/prepare-model.ps1" }
docker compose up --build -d
Write-Host "Откройте http://127.0.0.1:8080"

