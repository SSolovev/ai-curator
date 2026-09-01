$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

docker compose pull model
docker compose build --pull app
docker compose up -d

Write-Host "Обновление применено. Проверка: http://127.0.0.1:8080/healthz"
