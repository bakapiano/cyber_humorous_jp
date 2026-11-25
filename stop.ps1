$ErrorActionPreference = 'Stop'
if (Get-Service -Name 'YoumoWorkflow-*' -ErrorAction SilentlyContinue) {
    throw 'NSSM services are registered. Stop only the required YoumoWorkflow service at an idle boundary; do not kill its supervised child process.'
}
$taskState = Join-Path $PSScriptRoot 'data\run\processes.json'
if (-not (Test-Path -LiteralPath $taskState)) { Write-Output 'No managed process state.'; exit 0 }
$taskEntries = @(Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json)
$taskStopped = 0
foreach ($taskEntry in $taskEntries) {
    $taskProcess = Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue
    if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$taskEntry.started).UtcTicks) {
        & taskkill.exe /PID $taskEntry.pid /T /F 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            # A child can exit while taskkill enumerates the tree. Recheck the exact
            # recorded process identity instead of treating an already-exited tree as live.
            $taskRemaining = Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue
            if ($taskRemaining -and $taskRemaining.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$taskEntry.started).UtcTicks) {
                throw "Could not stop managed process $($taskEntry.name)"
            }
        }
        $taskStopped += 1
    }
}
Write-Output "Stopped $taskStopped matching workflow process trees. Source modules and data were preserved."
