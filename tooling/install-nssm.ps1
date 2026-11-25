param([switch]$Preview)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'nssm-config.ps1')
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskNssm = (Get-Command nssm.exe).Source
$taskServices = @(Get-WorkflowServices -Root $taskRoot -Node (Get-Command node.exe).Source)
if ($Preview) { $taskServices | ConvertTo-Json; return }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer as the current Windows user with administrator privileges.'
}
foreach ($taskSpec in $taskServices) {
    if (Get-Service -Name $taskSpec.name -ErrorAction SilentlyContinue) { throw 'Workflow service already registered; inspect it rather than overwrite it.' }
    if (-not (Test-Path -LiteralPath $taskSpec.application -PathType Leaf)) { throw 'A required runtime is missing.' }
}
foreach ($taskFile in @('frontend/dist/index.html','services/youtube/dist/server.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $taskFile))) { throw 'Build the workflow before installation.' }
}
$taskAccount = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskSecret = Read-Host "Windows password for $taskAccount (hidden input; never saved in scripts)" -AsSecureString
$taskCredential = [PSCredential]::new($taskAccount, $taskSecret)
$taskPython = Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
$taskLogs = Join-Path $taskRoot 'data/run/services'
New-Item -ItemType Directory -Force -Path $taskLogs | Out-Null
$taskRegistered = @()
$taskBoundary = $false
Push-Location $taskRoot
try {
    $taskProxy = & $taskPython -c "from youmo_workflow.config import existing_proxy,get_settings; print(existing_proxy(get_settings()) or '')"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve existing proxy configuration.' }
    $taskEnvironment = @("PATH=$env:PATH", "USERPROFILE=$env:USERPROFILE", "APPDATA=$env:APPDATA", "LOCALAPPDATA=$env:LOCALAPPDATA", 'PYTHONUNBUFFERED=1')
    if ($taskProxy) { $taskEnvironment += "WORKFLOW_PROXY=$($taskProxy.Trim())" }
    foreach ($taskSpec in $taskServices) {
        $taskName = $taskSpec.name
        Invoke-WorkflowNssm $taskNssm @('install', $taskName, $taskSpec.application)
        $taskRegistered += $taskName
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'Start', 'SERVICE_DEMAND_START')
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppDirectory', $taskRoot)
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppParameters', $taskSpec.arguments)
        Invoke-WorkflowNssm $taskNssm (@('set', $taskName, 'AppEnvironmentExtra') + $taskEnvironment)
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppStdout', (Join-Path $taskLogs "$($taskSpec.role).stdout.log"))
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppStderr', (Join-Path $taskLogs "$($taskSpec.role).stderr.log"))
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppRotateFiles', '1')
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppRotateOnline', '1')
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppRotateBytes', '33554432')
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppExit', 'Default', 'Restart')
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'AppRestartDelay', '10000')
        # NSSM grants SeServiceLogonRight for this account; no real password goes in argv.
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'ObjectName', $taskAccount, '')
        Set-Service -Name $taskName -Credential $taskCredential
    }
    foreach ($taskName in @('YoumoWorkflow-Worker', 'YoumoWorkflow-Scanner')) {
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'DependOnService', 'YoumoWorkflow-YouTube')
    }
    Confirm-WorkflowServiceLogon -Credential $taskCredential
    & $taskPython tooling/service_boundary.py acquire
    if ($LASTEXITCODE -ne 0) { throw 'Work is active; services remain manual and legacy processes stay running.' }
    $taskBoundary = $true
    Stop-LegacyWorkflow -Root $taskRoot
    $taskArchive = Join-Path $taskRoot ('data/run/legacy-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $taskArchive | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $taskRoot 'data/run') -Filter '*.log' -File | Copy-Item -Destination $taskArchive
    foreach ($taskSpec in $taskServices) {
        Start-Service -Name $taskSpec.name
        (Get-Service -Name $taskSpec.name).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
    foreach ($taskName in $taskRegistered) {
        Invoke-WorkflowNssm $taskNssm @('set', $taskName, 'Start', 'SERVICE_DELAYED_AUTO_START')
    }
    $taskServices | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'data/run/services.json') -Encoding UTF8
    Write-Output 'All five NSSM services are running with delayed automatic startup.'
} catch {
    foreach ($taskName in $taskRegistered) {
        Set-Service -Name $taskName -StartupType Manual -ErrorAction SilentlyContinue
    }
    Write-Error 'Service migration did not complete. Registered services are manual; inspect status before retrying. No journals or task counters were reset.'
    throw
} finally {
    if ($taskBoundary) { & $taskPython tooling/service_boundary.py release }
    $taskCredential = $null
    $taskSecret.Dispose()
    Pop-Location
}
