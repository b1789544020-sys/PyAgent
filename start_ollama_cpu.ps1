[CmdletBinding()]
param(
    [string]$OllamaExe = "ollama.exe",
    [string]$ModelsDir = ".ollama_models",
    [string]$DataDir = ".pyagent-data",
    [string]$Listen = "127.0.0.1:11434",
    [ValidateRange(1, 4)]
    [int]$NumParallel = 1
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://$Listen"

function Test-LocalService {
    try {
        $null = Invoke-RestMethod -Uri "$baseUrl/api/version" -TimeoutSec 2
        return $true
    }
    catch {
        return $false
    }
}

if (Test-LocalService) {
    $models = Invoke-RestMethod -Uri "$baseUrl/api/tags" -TimeoutSec 5
    [PSCustomObject]@{
        status = "already_running"
        url = $baseUrl
        models = @($models.models | ForEach-Object { $_.name })
        note = "复用现有服务；未修改系统环境变量。"
    } | ConvertTo-Json -Depth 4
    exit 0
}

if (-not (Test-Path -LiteralPath $OllamaExe -PathType Leaf)) {
    throw "找不到 Ollama：$OllamaExe。请用 -OllamaExe 指定实际路径。"
}
if (-not (Test-Path -LiteralPath $ModelsDir -PathType Container)) {
    throw "找不到模型目录：$ModelsDir。请用 -ModelsDir 指定实际路径；脚本不会联网下载模型。"
}

$logDir = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# 只影响本次启动的子进程：强制 CPU、禁用云端模型，避免同时加载两个大模型。
$env:OLLAMA_HOST = $Listen
$env:OLLAMA_MODELS = $ModelsDir
$env:OLLAMA_LLM_LIBRARY = "cpu"
$env:CUDA_VISIBLE_DEVICES = ""
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_NUM_PARALLEL = [string]$NumParallel
$env:OLLAMA_KEEP_ALIVE = "10m"

$process = Start-Process -FilePath $OllamaExe `
    -ArgumentList @("serve") `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "ollama-cpu.out.log") `
    -RedirectStandardError (Join-Path $logDir "ollama-cpu.err.log") `
    -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) {
        break
    }
    if (Test-LocalService) {
        $ready = $true
        break
    }
}
if (-not $ready) {
    $tail = Get-Content -LiteralPath (Join-Path $logDir "ollama-cpu.err.log") -Tail 20 -ErrorAction SilentlyContinue
    throw "纯 CPU Ollama 未能启动。日志末尾：`n$($tail -join [Environment]::NewLine)"
}

$models = Invoke-RestMethod -Uri "$baseUrl/api/tags" -TimeoutSec 5
[PSCustomObject]@{
    status = "started"
    pid = $process.Id
    url = $baseUrl
    models_dir = $ModelsDir
    models = @($models.models | ForEach-Object { $_.name })
    cpu_only = $true
    cloud_models = $false
    log_dir = $logDir
} | ConvertTo-Json -Depth 4
