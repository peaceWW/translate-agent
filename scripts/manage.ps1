<#
.SYNOPSIS
  统一入口：setup | start | stop | status | restart

.EXAMPLE
  .\scripts\manage.ps1 start
  .\scripts\manage.ps1 stop backend
  .\scripts\manage.ps1 status
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "start", "stop", "status", "restart")]
    [string]$Command = "status",

    [Parameter(Position = 1)]
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all",

    [switch]$SkipWait
)

$ErrorActionPreference = "Stop"

switch ($Command) {
    "setup" { & "$PSScriptRoot\setup.ps1" }
    "start" { & "$PSScriptRoot\start.ps1" -Target $Target -SkipWait:$SkipWait }
    "stop" { & "$PSScriptRoot\stop.ps1" -Target $Target }
    "status" { & "$PSScriptRoot\status.ps1" }
    "restart" { & "$PSScriptRoot\restart.ps1" -Target $Target }
}
