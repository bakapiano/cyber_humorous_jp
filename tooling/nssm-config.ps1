function Get-WorkflowServices {
    param([string]$Root, [string]$Node)
    $taskPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'
    @(
        @{role='youtube'; name='YoumoWorkflow-YouTube'; application=$Node; arguments='services/youtube/dist/server.js'},
        @{role='api'; name='YoumoWorkflow-Api'; application=$taskPython; arguments='-m uvicorn youmo_workflow.api:app --host 127.0.0.1 --port 8766'},
        @{role='publisher'; name='YoumoWorkflow-Publisher'; application=$taskPython; arguments='-m youmo_workflow.publisher_worker'},
        @{role='worker'; name='YoumoWorkflow-Worker'; application=$taskPython; arguments='-m youmo_workflow.worker --production-only'},
        @{role='scanner'; name='YoumoWorkflow-Scanner'; application=$taskPython; arguments='-m youmo_workflow.scan_worker'}
    )
}

function Invoke-WorkflowNssm {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'NSSM configuration failed; existing journals are untouched.' }
}

function Confirm-WorkflowServiceLogon {
    param([PSCredential]$Credential)
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WorkflowServiceLogon {
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool LogonUserW(string user, string domain, IntPtr password, int type, int provider, out IntPtr token);
    [DllImport("kernel32.dll")]
    public static extern bool CloseHandle(IntPtr handle);
}
'@
    $taskParts = $Credential.UserName.Split('\', 2)
    $taskMemory = [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($Credential.Password)
    $taskToken = [IntPtr]::Zero
    try {
        if (-not [WorkflowServiceLogon]::LogonUserW($taskParts[1], $taskParts[0], $taskMemory, 5, 0, [ref]$taskToken)) {
            throw 'Windows rejected the service logon. Legacy processes have not been stopped.'
        }
    } finally {
        if ($taskToken -ne [IntPtr]::Zero) { [WorkflowServiceLogon]::CloseHandle($taskToken) | Out-Null }
        [Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($taskMemory)
    }
}

function Stop-LegacyWorkflow {
    param([string]$Root)
    $taskState = Join-Path $Root 'data/run/processes.json'
    if (-not (Test-Path -LiteralPath $taskState)) { return }
    foreach ($taskEntry in @(Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json)) {
        $taskProcess = Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue
        if ($taskProcess -and $taskProcess.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$taskEntry.started).UtcTicks) {
            & taskkill.exe /PID $taskEntry.pid /T /F | Out-Null
            $taskRemaining = Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue
            if ($taskRemaining -and $taskRemaining.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$taskEntry.started).UtcTicks) {
                throw 'An identified legacy workflow process remains alive.'
            }
        }
    }
}
