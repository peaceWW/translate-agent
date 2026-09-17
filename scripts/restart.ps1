<#
.SYNOPSIS
  重启 AcademicTranslate 本地前后端服务。

.PARAMETER Target
  all | backend | frontend （默认 all）
#>
param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

Write-Host ">>> 重启本地环境 ($Target)"
& "$PSScriptRoot\stop.ps1" -Target $Target
Start-Sleep -Seconds 1
& "$PSScriptRoot\start.ps1" -Target $Target
