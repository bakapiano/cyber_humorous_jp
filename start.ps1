param([switch]$DevFrontend)
$ErrorActionPreference = 'Stop'
if (Get-Service -Name 'YoumoWorkflow-*' -ErrorAction SilentlyContinue) {
    throw 'NSSM services are registered. Use Start-Service YoumoWorkflow-*; do not start duplicate background workers.'
}
$taskRoot = $PSScriptRoot
$taskRun = Join-Path $taskRoot 'data\run'
New-Item -ItemType Directory -Force -Path $taskRun | Out-Null
$taskState = Join-Path $taskRun 'processes.json'
$taskPorts = @(8766, 8767)
if ($DevFrontend) { $taskPorts += 5173 }
foreach ($taskPort in $taskPorts) {
    if (@(Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "Port $taskPort is in use. No existing process was stopped."
    }
}
if (Test-Path -LiteralPath $taskState) {
    $taskPrevious = @(Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json)
    foreach ($taskEntry in $taskPrevious) {
        $taskProcess = Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue
        if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$taskEntry.started).UtcTicks) {
            throw 'A managed workflow process is already running. Use stop.ps1 first.'
        }
    }
}
Push-Location $taskRoot
try {
    & uv run --project backend python -m youmo_workflow.cli init
    if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' }
    if (-not $env:WORKFLOW_PROXY) {
        $taskProxy = & uv run --project backend python -c "from youmo_workflow.config import existing_proxy,get_settings; print(existing_proxy(get_settings()) or '')"
        if ($LASTEXITCODE -ne 0) { throw 'Proxy configuration check failed' }
        if ($taskProxy) { $env:WORKFLOW_PROXY = $taskProxy.Trim() }
    }
    if (-not (Test-Path 'frontend\dist\index.html')) {
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    }
    $taskSpecs = @(
        @{name='api'; file=(Get-Command uv.exe).Source; arguments=@('run','--project','backend','uvicorn','youmo_workflow.api:app','--host','127.0.0.1','--port','8766')},
        @{name='worker'; file=(Get-Command uv.exe).Source; arguments=@('run','--project','backend','python','-m','youmo_workflow.worker','--production-only')},
        @{name='publisher'; file=(Get-Command uv.exe).Source; arguments=@('run','--project','backend','python','-m','youmo_workflow.publisher_worker')},
        @{name='scanner'; file=(Get-Command uv.exe).Source; arguments=@('run','--project','backend','python','-m','youmo_workflow.scan_worker')},
        @{name='youtube'; file=(Get-Command node.exe).Source; arguments=@('services/youtube/dist/server.js')}
    )
    if ($DevFrontend) { $taskSpecs += @{name='frontend'; file=(Get-Command pnpm.cmd).Source; arguments=@('--filter','@youmo/frontend','dev')} }
    $taskStarted = @()
    foreach ($taskSpec in $taskSpecs) {
        $taskProcess = Start-Process -FilePath $taskSpec.file -ArgumentList $taskSpec.arguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRun "$($taskSpec.name).stdout.log") -RedirectStandardError (Join-Path $taskRun "$($taskSpec.name).stderr.log")
        $taskStarted += @{name=$taskSpec.name; pid=$taskProcess.Id; started=$taskProcess.StartTime.ToUniversalTime().ToString('o')}
        $taskStarted | ConvertTo-Json | Set-Content -LiteralPath $taskState -Encoding UTF8
    }
    Write-Output 'Workflow started: http://127.0.0.1:8766 (channel scanning and batch production have separate persisted switches).'
} finally { Pop-Location }
