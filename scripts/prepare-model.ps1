$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir
New-Item -ItemType Directory -Force -Path models,secrets,certs | Out-Null
if (-not (Test-Path "secrets/hmac_key")) { [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLower() | Set-Content -NoNewline "secrets/hmac_key" }
$Model = "models/Qwen3-4B-Q4_K_M.gguf"
$Url = "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/212c964b8f97cb5edc203d411b767aaae707e653/Qwen3-4B-Q4_K_M.gguf"
if (-not (Test-Path $Model)) { Write-Host "Скачивание модели один раз (5.03 GB)…"; curl.exe -L --fail -C - -o "$Model.part" $Url; Move-Item "$Model.part" $Model }
$Actual = (Get-FileHash -Algorithm SHA256 $Model).Hash.ToLower()
if ($Actual -ne "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785") { throw "Checksum модели не совпал. Файл удалять автоматически не будем." }
Write-Host "Модель подготовлена и проверена."

