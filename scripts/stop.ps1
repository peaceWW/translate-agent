<#
.SYNOPSIS
  停止 AcademicTranslate 本地前后端服务。

.PARAMETER Target
  all | backend | frontend （默认 all）
#>
param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

Write-Host ">>> 停止本地环境 ($Target)"

if ($Target -in @("all", "frontend")) {
    Stop-ServiceByName "frontend" $FrontendPort
}

if ($Target -in @("all", "backend")) {
    Stop-ServiceByName "backend" $BackendPort
}

Start-Sleep -Seconds 1
Show-Status
