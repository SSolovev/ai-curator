$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama не найдена. Установите и запустите native Ollama."
}

function New-LocalGgufModel(
    [string]$ModelName,
    [string]$GgufName,
    [string]$ModelfileName
) {
    $GgufPath = Join-Path $ProjectDir "models/$GgufName"
    $ModelfilePath = Join-Path $ProjectDir "models/$ModelfileName"
    if (-not (Test-Path $GgufPath)) { throw "Не найден models/$GgufName" }
    if (-not (Test-Path $ModelfilePath)) { throw "Не найден models/$ModelfileName" }
    & ollama create $ModelName -f $ModelfilePath
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать модель $ModelName" }
    & ollama show $ModelName *> $null
    if ($LASTEXITCODE -ne 0) { throw "Не удалось проверить модель $ModelName" }
}

function New-RegistryModel(
    [string]$ModelName,
    [string]$SourceName,
    [string]$ModelfileName
) {
    $ModelfilePath = Join-Path $ProjectDir "models/$ModelfileName"
    if (-not (Test-Path $ModelfilePath)) { throw "Не найден models/$ModelfileName" }
    & ollama pull $SourceName
    if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать модель $SourceName" }
    & ollama create $ModelName -f $ModelfilePath
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать модель $ModelName" }
    & ollama show $ModelName *> $null
    if ($LASTEXITCODE -ne 0) { throw "Не удалось проверить модель $ModelName" }
}

if ($args.Count -gt 1) {
    throw "Использование: scripts/prepare-ollama.ps1 [gemma4-12b|qwen3-8b|pii-filter|all]"
}

$Target = if ($args.Count -eq 0) { "gemma4-12b" } else { $args[0] }

switch ($Target) {
    "qwen3-8b" {
        New-LocalGgufModel "ai-curator-qwen3-8b" "Qwen3-8B-Q4_K_M.gguf" "Modelfile.qwen3-8b"
    }
    "gemma4-12b" {
        New-RegistryModel "ai-curator-gemma4-12b" "gemma4:12b" "Modelfile.gemma4-12b"
    }
    "pii-filter" {
        New-RegistryModel "ai-curator-pii-gemma3-4b" "gemma3:4b" "Modelfile.pii-gemma3-4b"
    }
    "all" {
        New-LocalGgufModel "ai-curator-qwen3-8b" "Qwen3-8B-Q4_K_M.gguf" "Modelfile.qwen3-8b"
        New-RegistryModel "ai-curator-gemma4-12b" "gemma4:12b" "Modelfile.gemma4-12b"
    }
    default {
        throw "Неизвестная модель: $Target. Допустимо: gemma4-12b, qwen3-8b, pii-filter или all"
    }
}

Write-Host "Подготовка Ollama завершена. Выберите CURATOR_OLLAMA_MODEL в .env и выполните scripts/start.ps1."
