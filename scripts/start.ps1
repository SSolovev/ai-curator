$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Get-Setting([string]$Name, [string]$Default) {
    $ProcessValue = [Environment]::GetEnvironmentVariable($Name)
    if ($ProcessValue) { return $ProcessValue }
    if (Test-Path ".env") {
        $Prefix = "$Name="
        $Line = Get-Content ".env" | Where-Object { $_.StartsWith($Prefix) } | Select-Object -Last 1
        if ($Line) {
            $FileValue = $Line.Substring($Prefix.Length).Trim().Trim('"').Trim("'")
            if ($FileValue) { return $FileValue }
        }
    }
    return $Default
}

function Assert-SecretFile([string]$ContainerPath) {
    if (-not $ContainerPath.StartsWith("/secrets/") -or $ContainerPath.Contains("..") -or $ContainerPath.EndsWith("/")) {
        throw "Ожидается путь к файлу внутри /secrets/."
    }
    $LocalPath = Join-Path "secrets" $ContainerPath.Substring("/secrets/".Length)
    if (-not (Test-Path $LocalPath -PathType Leaf) -or (Get-Item $LocalPath).Length -eq 0) {
        throw "Создайте непустой $LocalPath."
    }
}

$Runtime = Get-Setting "CURATOR_LLM_RUNTIME" "llama_cpp"
$Port = Get-Setting "CURATOR_PORT" "8080"
$MainModel = Get-Setting "CURATOR_OLLAMA_MODEL" "ai-curator-gemma4-12b"
$PiiModel = Get-Setting "CURATOR_PII_FILTER_MODEL" "ai-curator-pii-gemma3-4b"
$PiiDictionary = Get-Setting "CURATOR_PII_FILTER_DICTIONARY_FILE" "/secrets/pii_dictionary.txt"

if (-not (Test-Path "secrets/hmac_key" -PathType Leaf) -or (Get-Item "secrets/hmac_key").Length -eq 0) {
    throw "Сначала подготовьте secrets/hmac_key по инструкции установки."
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Установите Ollama для обязательного PII-фильтра."
}
& ollama show $PiiModel *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PII-модель $PiiModel не найдена. Выполните scripts/prepare-ollama.ps1 pii-filter"
}
Assert-SecretFile $PiiDictionary

switch ($Runtime) {
    "llama_cpp" {
        if (-not (Test-Path "models/Qwen3-4B-Q4_K_M.gguf" -PathType Leaf)) {
            throw "Сначала выполните scripts/prepare-model.ps1"
        }
    }
    "ollama" {
        & ollama show $MainModel *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Модель $MainModel не найдена. Выполните scripts/prepare-ollama.ps1"
        }
    }
    "external" {
        if ((Get-Setting "CURATOR_EXTERNAL_LLM_ENABLED" "false") -notin @("true", "1", "yes")) {
            throw "Для external установите CURATOR_EXTERNAL_LLM_ENABLED=true."
        }
        Assert-SecretFile (Get-Setting "CURATOR_EXTERNAL_LLM_API_KEY_FILE" "/secrets/external_llm_api_key")
    }
    default {
        throw "Неизвестный runtime $Runtime. Допустимо: llama_cpp, ollama или external."
    }
}

docker compose stop app
if ($Runtime -ne "llama_cpp") {
    docker compose --profile llama stop model
}
$ModelsToStop = @($MainModel, $PiiModel, "ai-curator-qwen3-4b", "ai-curator-qwen3-8b", "ai-curator-gemma4-12b", "ai-curator-pii-gemma3-4b")
foreach ($Model in ($ModelsToStop | Select-Object -Unique)) {
    & ollama stop $Model *> $null
}

if ($Runtime -eq "llama_cpp") {
    docker compose --profile llama --profile demo up --build -d app model mock-jira
} else {
    docker compose --profile demo up --build -d --no-deps app mock-jira
}
if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить сервис" }
Write-Host "ИИ-Куратор запускается ($Runtime). Откройте http://127.0.0.1:$Port"
