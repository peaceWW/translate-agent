# Shared helpers for AcademicTranslate local project management.
# Dot-source from other scripts: . "$PSScriptRoot\common.ps1"

$ErrorActionPreference = "Stop"
try {
    chcp 65001 > $null
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    # ignore console encoding failures
}

$Script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$Script:RuntimeDir = Join-Path $ProjectRoot ".runtime"
$Script:BackendDir = Join-Path $ProjectRoot "backend"
$Script:FrontendDir = Join-Path $ProjectRoot "frontend"
$Script:BackendPort = 8080
$Script:FrontendPort = 5173
$Script:BackendHost = "127.0.0.1" # Loopback is for health checks only.
$Script:ListenAddress = "0.0.0.0"

function Ensure-RuntimeDir {
    if (-not (Test-Path $RuntimeDir)) {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    }
}

function Get-PidFile([string]$Name) {
    return Join-Path $RuntimeDir "$Name.pid"
}

function Get-LogFile([string]$Name) {
    return Join-Path $RuntimeDir "$Name.log"
}

function Read-Pid([string]$Name) {
    $file = Get-PidFile $Name
    if (-not (Test-Path $file)) { return $null }
    $raw = (Get-Content $file -Raw).Trim()
    if (-not $raw) { return $null }
    try { return [int]$raw } catch { return $null }
}

function Write-Pid([string]$Name, [int]$ProcessId) {
    Ensure-RuntimeDir
    Set-Content -Path (Get-PidFile $Name) -Value $ProcessId -Encoding ascii
}

function Clear-Pid([string]$Name) {
    $file = Get-PidFile $Name
    if (Test-Path $file) { Remove-Item $file -Force }
}

function Test-ProcessRunning([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return $null -ne $p
    } catch {
        return $false
    }
}

function Get-ListeningPids([int]$Port) {
    $pids = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess) { $pids += [int]$c.OwningProcess }
        }
    } catch {
        # Fallback via netstat for older environments
        $lines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
        foreach ($line in $lines) {
            $parts = ($line.ToString() -split "\s+") | Where-Object { $_ }
            if ($parts.Count -ge 5) {
                $pids += [int]$parts[-1]
            }
        }
    }
    return @($pids | Select-Object -Unique)
}

function Test-HttpOk([string]$Url, [int]$TimeoutSec = 2) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Get-BackendPython {
    $venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { return $venvPython }
    return $null
}

function Ensure-BackendEnv {
    $envFile = Join-Path $BackendDir ".env"
    $example = Join-Path $BackendDir ".env.example"
    if (-not (Test-Path $envFile) -and (Test-Path $example)) {
        Copy-Item $example $envFile
        Write-Host "[setup] 已从 .env.example 复制 backend/.env"
    }
}

function Get-ServiceStatus([string]$Name, [int]$Port, [string]$HealthUrl) {
    $pidVal = Read-Pid $Name
    $pidAlive = $false
    if ($null -ne $pidVal) { $pidAlive = Test-ProcessRunning $pidVal }

    $portPids = Get-ListeningPids $Port
    $portOpen = $portPids.Count -gt 0
    $healthOk = $false
    if ($HealthUrl -and $portOpen) {
        $healthOk = Test-HttpOk $HealthUrl
    }

    $state = "stopped"
    if ($healthOk) { $state = "running" }
    elseif ($portOpen -or $pidAlive) { $state = "starting_or_unhealthy" }

    return [pscustomobject]@{
        Name      = $Name
        State     = $state
        Pid       = $pidVal
        PidAlive  = $pidAlive
        Port      = $Port
        PortOpen  = $portOpen
        PortPids  = ($portPids -join ",")
        HealthOk  = $healthOk
        LogFile   = (Get-LogFile $Name)
    }
}

function Stop-Tree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    # /T kills child processes (uvicorn reload / npm node tree)
    & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
}

function Stop-ServiceByName([string]$Name, [int]$Port) {
    $pidVal = Read-Pid $Name
    if ($null -ne $pidVal -and (Test-ProcessRunning $pidVal)) {
        Write-Host "[stop] 停止 $Name (pid=$pidVal)"
        Stop-Tree $pidVal
    }

    $portPids = Get-ListeningPids $Port
    foreach ($p in $portPids) {
        Write-Host "[stop] 释放端口 $Port (pid=$p)"
        Stop-Tree $p
    }

    Clear-Pid $Name
}

function Start-Backend {
    Ensure-RuntimeDir
    Ensure-BackendEnv

    $status = Get-ServiceStatus "backend" $BackendPort "http://$BackendHost`:$BackendPort/api/health"
    if ($status.State -eq "running") {
        Write-Host "[start] 后端已在运行 (port=$BackendPort)"
        return
    }
    if ($status.PortOpen) {
        throw "端口 $BackendPort 已被占用 (pids=$($status.PortPids))，请先执行 stop 或释放端口"
    }

    $python = Get-BackendPython
    if (-not $python) {
        throw "未找到 backend/.venv。请先运行: .\scripts\setup.ps1"
    }

    $log = Get-LogFile "backend"
    $launcher = Join-Path $RuntimeDir "start-backend.cmd"
    $lines = @(
        "@echo off",
        "cd /d `"$BackendDir`"",
        "`"$python`" -m uvicorn app.main:app --host $ListenAddress --port $BackendPort --reload > `"$log`" 2>&1"
    )
    Set-Content -Path $launcher -Value $lines -Encoding ASCII

    Write-Host "[start] 启动后端 -> http://$BackendHost`:$BackendPort"
    $proc = Start-Process -FilePath $launcher -WorkingDirectory $BackendDir -PassThru -WindowStyle Hidden
    Write-Pid "backend" $proc.Id
    Write-Host "[start] 后端 pid=$($proc.Id)，日志: $log"
}

function Start-Frontend {
    Ensure-RuntimeDir

    $status = Get-ServiceStatus "frontend" $FrontendPort "http://$BackendHost`:$FrontendPort/"
    if ($status.State -eq "running") {
        Write-Host "[start] 前端已在运行 (port=$FrontendPort)"
        return
    }
    if ($status.PortOpen) {
        throw "端口 $FrontendPort 已被占用 (pids=$($status.PortPids))，请先执行 stop 或释放端口"
    }

    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        throw "未找到 npm，请先安装 Node.js"
    }

    $nodeModules = Join-Path $FrontendDir "node_modules"
    if (-not (Test-Path $nodeModules)) {
        throw "未找到 frontend/node_modules。请先运行: .\scripts\setup.ps1"
    }

    $log = Get-LogFile "frontend"
    $launcher = Join-Path $RuntimeDir "start-frontend.cmd"
    $lines = @(
        "@echo off",
        "cd /d `"$FrontendDir`"",
        "npm run dev -- --host $ListenAddress --port $FrontendPort --strictPort > `"$log`" 2>&1"
    )
    Set-Content -Path $launcher -Value $lines -Encoding ASCII

    Write-Host "[start] 启动前端 -> http://$BackendHost`:$FrontendPort"
    $proc = Start-Process -FilePath $launcher -WorkingDirectory $FrontendDir -PassThru -WindowStyle Hidden
    Write-Pid "frontend" $proc.Id
    Write-Host "[start] 前端 pid=$($proc.Id)，日志: $log"
}

function Wait-ServiceReady([string]$Name, [int]$Port, [string]$HealthUrl, [int]$TimeoutSec = 45) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $s = Get-ServiceStatus $Name $Port $HealthUrl
        if ($s.State -eq "running") {
            Write-Host "[ready] $Name 已就绪"
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "[warn] $Name 在 ${TimeoutSec}s 内未就绪，请查看日志: $(Get-LogFile $Name)"
    return $false
}

function Show-Status {
    $backend = Get-ServiceStatus "backend" $BackendPort "http://$BackendHost`:$BackendPort/api/health"
    $frontend = Get-ServiceStatus "frontend" $FrontendPort "http://$BackendHost`:$FrontendPort/"

    Write-Host ""
    Write-Host "AcademicTranslate 本地环境状态"
    Write-Host ("=" * 56)
    foreach ($s in @($backend, $frontend)) {
        $health = if ($s.HealthOk) { "OK" } else { "NO" }
        $pidText = if ($null -ne $s.Pid) { $s.Pid } else { "-" }
        Write-Host ("{0,-9} {1,-22} pid={2,-8} port={3,-5} health={4}" -f $s.Name, $s.State, $pidText, $s.Port, $health)
        if ($s.PortPids) {
            Write-Host ("          listening pids: {0}" -f $s.PortPids)
        }
        Write-Host ("          log: {0}" -f $s.LogFile)
    }
    Write-Host ("=" * 56)
    Write-Host "后端健康: http://$BackendHost`:$BackendPort/api/health"
    Write-Host "前端页面: http://$BackendHost`:$FrontendPort/"
    Write-Host "监听地址: $ListenAddress（前端 $FrontendPort / 后端 $BackendPort）"
    Write-Host "其他设备访问: http://<本机局域网 IP>:$FrontendPort/"
    Write-Host ""
}
