param([switch]$SkipBrowser)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskRoot = Split-Path $PSScriptRoot -Parent

function Invoke-Check {
    param([string]$Command, [string[]]$CommandArguments)
    & $Command @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Check failed: $Command $($CommandArguments -join ' ')" }
}

Push-Location $taskRoot
try {
    Invoke-Check uv @('run', '--project', 'backend', 'python', 'tooling/policy_check.py')
    Invoke-Check pnpm @('--filter', '@youmo/browser-bridge', 'build')
    Invoke-Check pnpm @('format:check')
    Invoke-Check pnpm @('lint')
    Invoke-Check pnpm @('typecheck')
    Invoke-Check pnpm @('typecheck:e2e')
    Invoke-Check pnpm @('test')
    Invoke-Check uv @('run', '--project', 'backend', 'ruff', 'format', '--check', 'backend/src', 'backend/tests', 'backend/typings', 'backend/migrations', 'tooling')
    Invoke-Check uv @('run', '--project', 'backend', 'ruff', 'check', 'backend/src', 'backend/tests', 'backend/typings', 'backend/migrations', 'tooling')
    Invoke-Check uv @('run', '--project', 'backend', 'pyright', '--project', 'backend')
    Invoke-Check uv @('run', '--project', 'backend', 'pylint', '--disable=all', '--enable=too-many-lines', '--max-module-lines=350', 'backend/src', 'backend/tests')
    Push-Location (Join-Path $taskRoot 'backend')
    try { Invoke-Check uv @('run', 'lint-imports') } finally { Pop-Location }
    Invoke-Check uv @('run', '--project', 'backend', 'pytest', '-q', 'backend/tests')
    Invoke-Check uv @('run', '--project', 'backend', 'python', '-m', 'pytest', '-q', 'tooling/test_service_boundary.py')
    Invoke-Check pwsh @('-NoProfile', '-File', 'tooling/test-nssm.ps1')
    Invoke-Check uv @('run', '--project', 'backend', 'python', '-m', 'youmo_workflow.cli', 'export-openapi', '--output', 'data/contract-check/openapi.json')
    Invoke-Check pnpm @('exec', 'openapi-typescript', 'data/contract-check/openapi.json', '-o', 'data/contract-check/api.ts')
    foreach ($taskPair in @(@('contracts/openapi.json', 'data/contract-check/openapi.json'), @('packages/api-types/src/generated/api.ts', 'data/contract-check/api.ts'))) {
        if ((Get-FileHash $taskPair[0]).Hash -ne (Get-FileHash $taskPair[1]).Hash) { throw "Generated contract drift: $($taskPair[0])" }
    }
    Invoke-Check pnpm @('build')
    if (-not $SkipBrowser) { Invoke-Check pnpm @('test:e2e') }
    Write-Output 'All requested quality gates passed.'
} finally { Pop-Location }
