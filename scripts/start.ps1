<#
.SYNOPSIS
  启动 AcademicTranslate 本地前后端服务。

.PARAMETER Target
  all | backend | frontend （默认 all）

.PARAMETER SkipWait
  启动后不等待健康检查
#>
param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all",
    [switch]$SkipWait
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

Write-Host ">>> 启动本地环境 ($Target)"

if ($Target -in @("all", "backend")) {
    Start-Backend
    if (-not $SkipWait) {
        Wait-ServiceReady "backend" $BackendPort "http://$BackendHost`:$BackendPort/api/health" | Out-Null
    }
}

if ($Target -in @("all", "frontend")) {
    Start-Frontend
    if (-not $SkipWait) {
        Wait-ServiceReady "frontend" $FrontendPort "http://$BackendHost`:$FrontendPort/" | Out-Null
    }
}

Show-Status
