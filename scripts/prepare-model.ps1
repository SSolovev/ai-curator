$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path models,secrets,certs | Out-Null

$KeyPath = "secrets/hmac_key"
if (-not (Test-Path $KeyPath) -or (Get-Item $KeyPath).Length -eq 0) {
    $Bytes = New-Object byte[] 32
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    } finally {
        $Generator.Dispose()
    }
    $Key = [BitConverter]::ToString($Bytes).Replace("-", "").ToLowerInvariant()
    Set-Content -Path $KeyPath -Value $Key -Encoding ascii -NoNewline
}

$ModelName = "Qwen3-4B-Q4_K_M.gguf"
$ModelPath = Join-Path "models" $ModelName
$Url = "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/$ModelName"
if (-not (Test-Path $ModelPath -PathType Leaf)) {
    Write-Host "Скачивание модели один раз (около 2,5 ГБ)…"
    curl.exe --fail --location --continue-at - --output "$ModelPath.part" $Url
    if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать GGUF." }
    Move-Item "$ModelPath.part" $ModelPath
}

$ManifestEntry = Get-Content "models/SHA256SUMS" | Where-Object { $_ -match "\s+$([regex]::Escape($ModelName))$" }
if (-not $ManifestEntry) { throw "В models/SHA256SUMS не найдена контрольная сумма $ModelName." }
$ExpectedHash = ($ManifestEntry -split "\s+")[0]
$ActualHash = (Get-FileHash -Algorithm SHA256 $ModelPath).Hash
if ($ActualHash -ne $ExpectedHash) { throw "Контрольная сумма модели не совпала. Проверьте файл $ModelPath." }
Write-Host "Модель подготовлена и проверена."
