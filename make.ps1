#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
    NADDP task runner for Windows — a shim for the canonical GNU Makefile.

.DESCRIPTION
    WHY THIS FILE EXISTS
    --------------------
    GNU make is not installed on the Windows build machine, and we do not want
    to make it a prerequisite for running the Ambassador demo on a laptop. The
    Makefile at the repo root is nevertheless the canonical task runner: it is
    what CI and Linux/macOS developers use. Rather than maintain two divergent
    workflows, this script exposes the IDENTICAL target list and delegates to
    the SAME underlying commands as the Makefile. If you change a recipe in one
    file, change it in the other in the same commit — no logic drift.

    Exit codes propagate from the underlying command, so CI-style usage works:
        .\make.ps1 test; if ($LASTEXITCODE -ne 0) { ... }

    Targets: help, install, db-up, db-down, migrate, dev, seed, demo-reset,
             demo-prewarm, test, lint, typecheck, gen-client, clean.

.EXAMPLE
    .\make.ps1
    Shows the self-documenting target list (default target).

.EXAMPLE
    .\make.ps1 dev
    Starts db + api (:8000) + web (:3000); Ctrl+C stops both children.

.EXAMPLE
    .\make.ps1 demo-reset
    Drops the schema, re-runs migrations and re-seeds the synthetic dataset.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Target = 'help'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Configuration — mirrors the overridable variables at the top of the Makefile.
# ---------------------------------------------------------------------------
$RepoRoot     = $PSScriptRoot
$ApiDir       = Join-Path $RepoRoot 'apps/api'
$ContractsDir = Join-Path $RepoRoot 'packages/contracts'
$SeedScript   = 'data/demo-seed/seed.py'
$ApiPort      = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
$WebPort      = if ($env:WEB_PORT) { $env:WEB_PORT } else { '3000' }
$ApiUrl       = "http://localhost:$ApiPort"
$Uv           = if ($env:UV) { $env:UV } else { 'uv' }
$Pnpm         = if ($env:PNPM) { $env:PNPM } else { 'pnpm' }
$ComposeExe   = 'docker'
$ComposeArgs  = @('compose')
$DbService    = 'db'
$DbUser       = 'naddp'
$DbName       = 'naddp'
$DbWaitTries  = 60

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Step {
    param([Parameter(Mandatory)][string] $Message)
    Write-Host "-> $Message" -ForegroundColor DarkGray
}

# Run a native command from $WorkingDirectory. A non-zero exit code ends the
# whole script with that same code, matching `make`'s fail-fast behaviour.
function Invoke-Step {
    param(
        [Parameter(Mandatory)][string] $Exe,
        [string[]] $Arguments = @(),
        [string] $WorkingDirectory = $RepoRoot
    )

    Write-Step (("{0} {1}" -f $Exe, ($Arguments -join ' ')).Trim())

    $code = 0
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    }
    catch [System.Management.Automation.CommandNotFoundException] {
        Pop-Location
        Write-Host "'$Exe' was not found on PATH — install it, or override it with the UV / PNPM environment variable." -ForegroundColor Red
        exit 127
    }
    catch {
        Pop-Location
        Write-Host "failed to run '$Exe': $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    Pop-Location

    if ($code -ne 0) {
        Write-Host "command failed (exit $code): $Exe $($Arguments -join ' ')" -ForegroundColor Red
        exit $code
    }
}

function Invoke-Compose {
    param([Parameter(Mandatory)][string[]] $Arguments)
    Invoke-Step -Exe $ComposeExe -Arguments ($ComposeArgs + $Arguments)
}

function Get-DbHealth {
    try {
        $output = & $ComposeExe @($ComposeArgs + @('ps', '--format', '{{.Health}}', $DbService)) 2>$null
        if ($null -eq $output) { return '' }
        $first = @($output) | Where-Object { $_ } | Select-Object -First 1
        if ($null -eq $first) { return '' }
        return $first.ToString().Trim()
    }
    catch {
        Write-Verbose "docker compose ps failed while polling health: $($_.Exception.Message)"
        return ''
    }
}

function Test-ApiUp {
    try {
        $null = Invoke-WebRequest -Uri "$ApiUrl/openapi.json" -UseBasicParsing -TimeoutSec 2
        return $true
    }
    catch {
        Write-Verbose "api not reachable at ${ApiUrl}: $($_.Exception.Message)"
        return $false
    }
}

# uvicorn --reload and `next dev` both fork children, so stop the whole tree.
function Stop-ProcessTree {
    param([Parameter(Mandatory)][System.Diagnostics.Process] $Process)

    if ($Process.HasExited) { return }

    try {
        & taskkill.exe '/PID' $Process.Id '/T' '/F' *> $null
    }
    catch {
        Write-Verbose "taskkill failed for PID $($Process.Id): $($_.Exception.Message)"
    }

    if (-not $Process.HasExited) {
        try {
            $Process.Kill($true)
        }
        catch {
            Write-Verbose "Kill failed for PID $($Process.Id): $($_.Exception.Message)"
        }
    }
}

# Start-Process launches an image, not a shell command: it cannot run a .ps1, and on
# PATH it takes the FIRST match rather than the first PATHEXT match. `pnpm` installs
# three shims side by side -- an extensionless Bash script, pnpm.cmd and pnpm.ps1 --
# so `Start-Process pnpm` picks the Bash script and dies with "%1 is not a valid Win32
# application", which is how `.\make.ps1 dev` failed to start the web server while every
# Invoke-Step call kept working (PowerShell's call operator understands .ps1). Resolve
# to a real .exe/.cmd/.bat before handing anything to Start-Process.
function Resolve-Launcher {
    param([Parameter(Mandatory)][string] $Exe)

    $launchable = @('.exe', '.com', '.cmd', '.bat')

    if ($Exe.Contains('\') -or $Exe.Contains('/')) {
        return $Exe
    }

    foreach ($candidate in @(Get-Command -Name $Exe -All -ErrorAction SilentlyContinue)) {
        if ($candidate.CommandType -ne 'Application') { continue }
        $extension = [System.IO.Path]::GetExtension($candidate.Source).ToLowerInvariant()
        if ($launchable -contains $extension) { return $candidate.Source }
    }

    # Nothing launchable on PATH: hand back the original name so Start-Process reports the
    # failure itself rather than this helper inventing a path that does not exist.
    return $Exe
}

function Remove-PathIfPresent {
    param([Parameter(Mandatory)][string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Step "removed $Path"
    }
    catch {
        Write-Warning "could not remove '$Path': $($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------------------
# Targets — same names, same order and same commands as the Makefile.
# ---------------------------------------------------------------------------
$Targets = [ordered]@{

    'help' = @{
        Help = 'Show this help'
        Run  = { Show-Help }
    }

    'install' = @{
        Help = 'Install JS + Python dependencies (pnpm workspace, uv project)'
        Run  = {
            Invoke-Step -Exe $Pnpm -Arguments @('install')
            Invoke-Step -Exe $Uv -Arguments @('sync') -WorkingDirectory $ApiDir
        }
    }

    'db-up' = @{
        Help = 'Start Postgres (pgvector) on host port 5433 and wait until healthy'
        Run  = {
            Invoke-Compose @('up', '-d', $DbService)
            Write-Host -NoNewline 'waiting for postgres to report healthy'
            for ($i = 0; $i -lt $DbWaitTries; $i++) {
                if ((Get-DbHealth) -eq 'healthy') {
                    Write-Host ' ok'
                    return
                }
                Write-Host -NoNewline '.'
                Start-Sleep -Seconds 1
            }
            Write-Host ''
            Write-Host "postgres was not healthy after $DbWaitTries s" -ForegroundColor Red
            & $ComposeExe @($ComposeArgs + @('logs', '--tail=50', $DbService))
            exit 1
        }
    }

    'db-down' = @{
        Help = 'Stop the database container (the named volume is preserved)'
        Run  = { Invoke-Compose @('down') }
    }

    'migrate' = @{
        Help = 'Apply all Alembic migrations (alembic upgrade head)'
        Run  = {
            Invoke-Step -Exe $Uv -Arguments @('run', 'alembic', 'upgrade', 'head') -WorkingDirectory $ApiDir
        }
    }

    'dev' = @{
        Help = 'Run db + api (:8000) + web (:3000) together; Ctrl+C stops all'
        Run  = {
            Invoke-Target -Name 'db-up'
            Invoke-Target -Name 'migrate'

            Write-Host ''
            Write-Host "api -> $ApiUrl"
            Write-Host "web -> http://localhost:$WebPort"
            Write-Host 'press Ctrl+C to stop both'
            Write-Host ''

            $api = $null
            $web = $null
            $canPollKeys = $true
            $previousCtrlC = $false

            # Take Ctrl+C over from the host so the finally block can stop the
            # children deterministically instead of being torn down with them.
            try {
                $previousCtrlC = [Console]::TreatControlCAsInput
                [Console]::TreatControlCAsInput = $true
            }
            catch {
                $canPollKeys = $false
                Write-Verbose "console key polling unavailable, using default Ctrl+C handling: $($_.Exception.Message)"
            }

            try {
                # Identical commands to the Makefile `dev` recipe. Both children
                # inherit this console, so their output streams straight through.
                $apiProcess = @{
                    FilePath         = Resolve-Launcher $Uv
                    ArgumentList     = @('run', 'uvicorn', 'app.main:app', '--reload', '--host', '0.0.0.0', '--port', $ApiPort)
                    WorkingDirectory = $ApiDir
                    NoNewWindow      = $true
                    PassThru         = $true
                }
                $api = Start-Process @apiProcess

                $webProcess = @{
                    FilePath         = Resolve-Launcher $Pnpm
                    ArgumentList     = @('--filter', '@naddp/web', 'dev', '--port', $WebPort)
                    WorkingDirectory = $RepoRoot
                    NoNewWindow      = $true
                    PassThru         = $true
                }
                $web = Start-Process @webProcess

                while ($true) {
                    if ($api.HasExited -or $web.HasExited) { break }

                    if ($canPollKeys) {
                        try {
                            if ([Console]::KeyAvailable) {
                                $key = [Console]::ReadKey($true)
                                if ($key.Key -eq 'C' -and ($key.Modifiers -band [ConsoleModifiers]::Control)) {
                                    Write-Host ''
                                    Write-Host 'stopping api + web...'
                                    break
                                }
                            }
                        }
                        catch {
                            $canPollKeys = $false
                            Write-Verbose "key polling disabled: $($_.Exception.Message)"
                        }
                    }

                    Start-Sleep -Milliseconds 200
                }
            }
            finally {
                foreach ($child in @($api, $web)) {
                    if ($null -ne $child) { Stop-ProcessTree -Process $child }
                }
                if ($canPollKeys) {
                    try {
                        [Console]::TreatControlCAsInput = $previousCtrlC
                    }
                    catch {
                        Write-Verbose "could not restore Ctrl+C handling: $($_.Exception.Message)"
                    }
                }
            }

            foreach ($child in @($api, $web)) {
                if ($null -ne $child -and $child.HasExited -and $child.ExitCode -ne 0) {
                    exit $child.ExitCode
                }
            }
        }
    }

    'seed' = @{
        Help = 'Load the synthetic demo dataset (hero thread + citation registry)'
        Run  = {
            Invoke-Step -Exe $Uv -Arguments @('run', '--project', 'apps/api', 'python', $SeedScript)
        }
    }

    'demo-prewarm' = @{
        Help = 'Generate and cache the hero morning briefs (run AFTER demo-reset)'
        Run  = {
            Write-Host 'warming the demo caches - the on-stage brief becomes a database read'
            Invoke-Step -Exe $Uv -Arguments @('run', 'python', '-m', 'app.cli.prewarm') -WorkingDirectory $ApiDir
        }
    }

    'demo-reset' = @{
        Help = 'Drop the schema, re-migrate and re-seed — restores a clean demo state'
        Run  = {
            Invoke-Target -Name 'db-up'
            Invoke-Compose @(
                'exec', '-T', $DbService, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', $DbUser, '-d', $DbName,
                '-c', 'DROP SCHEMA IF EXISTS public CASCADE;',
                '-c', 'CREATE SCHEMA public;',
                '-c', "GRANT ALL ON SCHEMA public TO $DbUser;",
                '-c', 'GRANT ALL ON SCHEMA public TO public;'
            )
            Invoke-Compose @(
                'exec', '-T', $DbService, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', $DbUser, '-d', $DbName,
                '-f', '/docker-entrypoint-initdb.d/001_extensions.sql'
            )
            Invoke-Target -Name 'migrate'
            Invoke-Target -Name 'seed'
            # The AI Gateway memoises snapshots AND misses, and the citation registry
            # with them. A reset that left either warm serves the previous seed's answer
            # against the new seed's evidence ids, which stage 8 then refuses — on stage.
            # The caches are process-local: this clears them here and prints the restart
            # a running API still needs.
            Invoke-Step -Exe $Uv -Arguments @('run', 'python', '-m', 'app.ai.reset') -WorkingDirectory $ApiDir
            Write-Host ''
            Write-Host 'demo-reset complete — clean seeded state restored' -ForegroundColor Green
        }
    }

    'test' = @{
        Help = 'Run the API test suite plus web lint + typecheck'
        Run  = {
            Invoke-Step -Exe $Uv -Arguments @('run', 'pytest') -WorkingDirectory $ApiDir
            Invoke-Step -Exe $Pnpm -Arguments @('--filter', '@naddp/web', 'lint')
            Invoke-Step -Exe $Pnpm -Arguments @('--filter', '@naddp/web', 'typecheck')
        }
    }

    'lint' = @{
        Help = 'ruff check + ruff format --check + mypy (api); eslint (web)'
        Run  = {
            Invoke-Step -Exe $Uv -Arguments @('run', 'ruff', 'check', '.') -WorkingDirectory $ApiDir
            Invoke-Step -Exe $Uv -Arguments @('run', 'ruff', 'format', '--check', '.') -WorkingDirectory $ApiDir
            Invoke-Step -Exe $Uv -Arguments @('run', 'mypy', 'app') -WorkingDirectory $ApiDir
            Invoke-Step -Exe $Pnpm -Arguments @('--filter', '@naddp/web', 'lint')
        }
    }

    'typecheck' = @{
        Help = 'mypy (api); tsc --noEmit (web)'
        Run  = {
            Invoke-Step -Exe $Uv -Arguments @('run', 'mypy', 'app') -WorkingDirectory $ApiDir
            Invoke-Step -Exe $Pnpm -Arguments @('--filter', '@naddp/web', 'typecheck')
        }
    }

    'gen-client' = @{
        Help = 'Generate the typed OpenAPI client into packages/contracts (starts api if needed)'
        Run  = {
            $temporaryApi = $null
            try {
                if (-not (Test-ApiUp)) {
                    Write-Host "api not reachable — starting a temporary instance on :$ApiPort"
                    $spawn = @{
                        FilePath         = Resolve-Launcher $Uv
                        ArgumentList     = @('run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $ApiPort)
                        WorkingDirectory = $ApiDir
                        NoNewWindow      = $true
                        PassThru         = $true
                    }
                    $temporaryApi = Start-Process @spawn
                    for ($i = 0; $i -lt 40; $i++) {
                        if (Test-ApiUp) { break }
                        Start-Sleep -Seconds 1
                    }
                }

                if (-not (Test-ApiUp)) {
                    Write-Host "could not fetch $ApiUrl/openapi.json" -ForegroundColor Red
                    exit 1
                }

                $generatedDir = Join-Path $ContractsDir 'src/generated'
                $null = New-Item -ItemType Directory -Force -Path $generatedDir
                $specPath = Join-Path $ContractsDir 'openapi.json'
                # There is exactly ONE generator: packages/contracts' own `generate` script.
                # It writes src/generated/openapi.d.ts, which is the file src/index.ts imports and
                # the package exports map points at. Invoking openapi-typescript directly from here
                # would emit a differently-named orphan file and leave the placeholder in place.
                $clientPath = Join-Path $generatedDir 'openapi.d.ts'

                try {
                    Invoke-WebRequest -Uri "$ApiUrl/openapi.json" -UseBasicParsing -OutFile $specPath
                }
                catch {
                    Write-Host "could not download the OpenAPI document: $($_.Exception.Message)" -ForegroundColor Red
                    exit 1
                }

                Invoke-Step -Exe $Pnpm -Arguments @('--filter', '@naddp/contracts', 'generate')
                Write-Host "wrote $clientPath" -ForegroundColor Green
            }
            finally {
                if ($null -ne $temporaryApi) { Stop-ProcessTree -Process $temporaryApi }
            }
        }
    }

    'clean' = @{
        Help = 'Remove build output, caches and generated client artefacts'
        Run  = {
            $paths = @(
                'apps/web/.next',
                'apps/web/out',
                'apps/web/coverage',
                'apps/web/tsconfig.tsbuildinfo',
                'packages/contracts/dist',
                # NOT packages/contracts/src/generated: that directory holds the committed
                # openapi.d.ts placeholder the workspace typechecks against before gen-client has
                # ever run. Deleting it dead-ends `pnpm typecheck` and `next build`.
                'packages/contracts/openapi.json',
                '.turbo',
                'node_modules/.cache',
                'apps/api/.pytest_cache',
                'apps/api/.mypy_cache',
                'apps/api/.ruff_cache',
                'apps/api/htmlcov',
                'apps/api/.coverage'
            )
            foreach ($relative in $paths) {
                Remove-PathIfPresent (Join-Path $RepoRoot $relative)
            }

            try {
                Get-ChildItem -LiteralPath $RepoRoot -Directory -Recurse -Filter '__pycache__' |
                    Where-Object { $_.FullName -notmatch 'node_modules' } |
                    ForEach-Object { Remove-PathIfPresent $_.FullName }
            }
            catch {
                Write-Warning "could not sweep __pycache__ directories: $($_.Exception.Message)"
            }

            Write-Host 'clean' -ForegroundColor Green
        }
    }
}

function Show-Help {
    Write-Host ''
    Write-Host 'NADDP — Nigeria-Australia Digital Diplomacy Platform (DEMO / SYNTHETIC DATA ONLY)' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  usage: .\make.ps1 <target>'
    Write-Host ''
    foreach ($name in $Targets.Keys) {
        Write-Host ('  {0,-12} {1}' -f $name, $Targets[$name].Help)
    }
    Write-Host ''
    Write-Host '  ports: web 3000 · api 8000 · postgres 5433'
    Write-Host '  mirrors the canonical GNU Makefile — keep both in sync.'
    Write-Host ''
}

function Invoke-Target {
    param([Parameter(Mandatory)][string] $Name)

    if (-not $Targets.Contains($Name)) {
        Write-Host ''
        Write-Host "unknown target: '$Name'" -ForegroundColor Red
        Write-Host ''
        Write-Host 'valid targets:'
        foreach ($valid in $Targets.Keys) {
            Write-Host ('  {0,-12} {1}' -f $valid, $Targets[$valid].Help)
        }
        Write-Host ''
        Write-Host 'usage: .\make.ps1 <target>'
        Write-Host ''
        exit 2
    }

    & $Targets[$Name].Run
}

Invoke-Target -Name $Target
exit 0
