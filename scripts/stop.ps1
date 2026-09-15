$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

function Get-DotEnvValue([string]$Name, [string]$Default) {
    if (-not (Test-Path ".env")) { return $Default }
    $Prefix = "$Name="
    $Line = Get-Content ".env" | Where-Object { $_.StartsWith($Prefix) } | Select-Object -Last 1
    if (-not $Line) { return $Default }
    return $Line.Substring($Prefix.Length).Trim().Trim('"').Trim("'")
}

$QualityModel = Get-DotEnvValue "CURATOR_OLLAMA_MODEL" "ai-curator-gemma4-12b"

docker compose --profile llama --profile demo down
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    & ollama stop $QualityModel *> $null
    & ollama stop "ai-curator-qwen3-4b" *> $null
    & ollama stop "ai-curator-qwen3-8b" *> $null
    & ollama stop "ai-curator-gemma4-12b" *> $null
    & ollama stop "ai-curator-pii-gemma3-4b" *> $null
}
