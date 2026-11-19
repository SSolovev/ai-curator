$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

function Get-DotEnvValue([string]$Name) {
    if (-not (Test-Path ".env")) { return $null }
    $Prefix = "$Name="
    $Line = Get-Content ".env" | Where-Object { $_.StartsWith($Prefix) } | Select-Object -Last 1
    if (-not $Line) { return $null }
    return $Line.Substring($Prefix.Length).Trim().Trim('"').Trim("'")
}

function Get-Setting([string]$Name, [string]$Default) {
    $ProcessValue = [Environment]::GetEnvironmentVariable($Name)
    if ($ProcessValue) { return $ProcessValue }
    $FileValue = Get-DotEnvValue $Name
    if ($FileValue) { return $FileValue }
    return $Default
}

function Stop-OllamaModel([string]$Name) {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        & ollama stop $Name *> $null
    }
}

function Clear-CuratorOllamaModels([string]$QualityModel) {
    Stop-OllamaModel $QualityModel
    Stop-OllamaModel "ai-curator-qwen3-4b"
    Stop-OllamaModel "ai-curator-qwen3-8b"
    Stop-OllamaModel "ai-curator-gemma4-12b"
    Stop-OllamaModel "ai-curator-pii-gemma3-4b"
}

function Assert-OllamaModel([string]$Name) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw "Ollama не установлен. Установите Native Ollama и выполните scripts/prepare-ollama.ps1"
    }
    & ollama show $Name *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Модель $Name не найдена. Выполните scripts/prepare-ollama.ps1"
    }
}

$Runtime = Get-Setting "CURATOR_LLM_RUNTIME" "llama_cpp"
$QualityModel = Get-Setting "CURATOR_OLLAMA_MODEL" "ai-curator-gemma4-12b"
$PiiModel = Get-Setting "CURATOR_PII_FILTER_MODEL" "ai-curator-pii-gemma3-4b"
$ExternalEnabled = Get-Setting "CURATOR_EXTERNAL_LLM_ENABLED" "false"
$ExternalKeyFile = Get-Setting "CURATOR_EXTERNAL_LLM_API_KEY_FILE" "/secrets/external_llm_api_key"
$PiiDictionaryFile = Get-Setting "CURATOR_PII_FILTER_DICTIONARY_FILE" "/secrets/pii_dictionary.txt"

if (-not (Test-Path "secrets/hmac_key")) { throw "Сначала выполните scripts/prepare-model.ps1" }

# Every main runtime is behind the same local, fail-closed privacy boundary.
Assert-OllamaModel $PiiModel
if (-not $PiiDictionaryFile.StartsWith("/secrets/") -or $PiiDictionaryFile.Contains("..")) {
    throw "Путь PII-словаря должен находиться внутри /secrets/."
}
$HostPiiDictionary = Join-Path "secrets" $PiiDictionaryFile.Substring(9)
if (-not (Test-Path $HostPiiDictionary) -or (Get-Item $HostPiiDictionary).Length -eq 0) {
    throw "Создайте непустой $HostPiiDictionary: он обязателен для любого runtime."
}

switch ($Runtime) {
    "llama_cpp" {
        if (-not (Test-Path "models/Qwen3-4B-Q4_K_M.gguf")) { throw "Сначала выполните scripts/prepare-model.ps1" }
        docker compose stop app
        Clear-CuratorOllamaModels $QualityModel
        docker compose --profile llama --profile demo up --build -d app model mock-jira
    }
    "ollama" {
        Assert-OllamaModel $QualityModel
        docker compose stop app
        docker compose --profile llama stop model
        Clear-CuratorOllamaModels $QualityModel
        docker compose --profile demo up --build -d --no-deps app mock-jira
    }
    "external" {
        if ($ExternalEnabled -notin @("true", "True", "TRUE", "1", "yes", "Yes", "YES")) {
            throw "Для external установите CURATOR_EXTERNAL_LLM_ENABLED=true."
        }
        if (-not $ExternalKeyFile.StartsWith("/secrets/") -or $ExternalKeyFile.Contains("..")) {
            throw "Путь API-ключа должен находиться внутри /secrets/."
        }
        $HostExternalKey = Join-Path "secrets" $ExternalKeyFile.Substring(9)
        if (-not (Test-Path $HostExternalKey) -or (Get-Item $HostExternalKey).Length -eq 0) {
            throw "Создайте $HostExternalKey с API-ключом внешней модели."
        }
        docker compose stop app
        docker compose --profile llama stop model
        Clear-CuratorOllamaModels $QualityModel
        docker compose --profile demo up --build -d --no-deps app mock-jira
    }
    default {
        throw "Неизвестный CURATOR_LLM_RUNTIME=$Runtime. Допустимо: llama_cpp, ollama или external"
    }
}

if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить сервис" }
Write-Host "ИИ-Куратор запускается с runtime=$Runtime. Откройте http://127.0.0.1:8080"
