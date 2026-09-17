<#
.SYNOPSIS
  初始化本地开发环境：Python venv、依赖、.env、前端 npm install。
#>
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

Write-Host ">>> 初始化本地环境"

# Backend venv
$venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[setup] 创建 backend/.venv ..."
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "未找到 python，请先安装 Python 3.10+" }
    & python -m venv (Join-Path $BackendDir ".venv")
    $venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
} else {
    Write-Host "[setup] backend/.venv 已存在"
}

Write-Host "[setup] 安装后端依赖 ..."
& $venvPython -m pip install -U pip
& $venvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")

Ensure-BackendEnv

# Frontend
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) { throw "未找到 npm，请先安装 Node.js" }

$nodeModules = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path $nodeModules)) {
    Write-Host "[setup] 安装前端依赖 (npm install) ..."
    Push-Location $FrontendDir
    try { & npm install } finally { Pop-Location }
} else {
    Write-Host "[setup] frontend/node_modules 已存在（如需重装请手动 npm install）"
}

Ensure-RuntimeDir
Write-Host ""
Write-Host "初始化完成。接下来可执行:"
Write-Host "  .\scripts\start.ps1"
Write-Host "  .\scripts\status.ps1"
Write-Host "  .\scripts\stop.ps1"
