$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

docker compose --profile llama pull model
docker compose build --pull app
& "$ProjectDir/scripts/start.ps1"

Write-Host "Обновление применено. Проверка: http://127.0.0.1:8080/healthz"
