$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskRoot = Split-Path $PSScriptRoot -Parent
foreach ($taskFile in @('tooling/install-nssm.ps1','tooling/nssm-config.ps1','start.ps1','stop.ps1')) {
    $taskParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $taskRoot $taskFile), [ref]$null, [ref]$taskParseErrors) | Out-Null
    if ($taskParseErrors.Count) { throw 'PowerShell script syntax failed.' }
}
. (Join-Path $PSScriptRoot 'nssm-config.ps1')
$taskSpecs = @(Get-WorkflowServices -Root 'C:\Synthetic Workflow' -Node 'C:\Synthetic Node\node.exe')
if ($taskSpecs.Count -ne 5 -or @($taskSpecs.name | Select-Object -Unique).Count -ne 5) { throw 'Expected five unique service roles.' }
if (@($taskSpecs | Where-Object role -eq worker)[0].arguments -notmatch '--production-only') { throw 'Producer must not consume publisher tasks.' }
if (@($taskSpecs | Where-Object role -eq publisher)[0].arguments -ne '-m youmo_workflow.publisher_worker') { throw 'Publisher role changed.' }
function Get-Service { param($Name) return 'synthetic-registered-service' }
foreach ($taskFile in @('start.ps1', 'stop.ps1')) {
    $taskBlocked = $false
    try { & (Join-Path $taskRoot $taskFile) } catch { $taskBlocked = $_.Exception.Message -like 'NSSM services are registered*' }
    if (-not $taskBlocked) { throw 'Legacy scripts must refuse managed service duplication or child termination.' }
}
Write-Output 'NSSM script syntax, roles and legacy guards passed (no service/process mutations).'
