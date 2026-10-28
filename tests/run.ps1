# Runs every check on Windows in a fresh virtual environment: Ruff, the Python suite with zero skips, then the
# gallery's lint, build, build-output check and Playwright suite. Written for Windows PowerShell 5.1, which the
# installer tests run the PowerShell scripts in.
# Needs Python 3.14, Git for Windows (its bash and perl run the macOS installer tests) and Node 24. CI runs it on
# a fresh machine; on a developer's PC, tests/sandbox.ps1 runs it in Windows Sandbox.
#   tests\run.ps1 [-Work <directory>] [-Python <interpreter command>] [-Bare]
# -Python defaults to the python on PATH. Outside CI and the sandbox the script stops and points at
# tests\sandbox.ps1: a run on a developer's PC depends on what the PC has installed, and every PowerShell the
# tests start opens a console window of its own when the run has no console. -Bare runs it anyway.
param(
    [string]$Work = (Join-Path $env:TEMP 'apt-scraper-tests'),
    [string]$Python = '',
    [switch]$Bare
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not ($Bare -or $env:CI -or $env:USERNAME -eq 'WDAGUtilityAccount')) {
    Write-Host 'This PC is neither CI nor a Windows Sandbox. Run the checks in a sandbox:' -ForegroundColor Red
    Write-Host '  powershell -ExecutionPolicy Bypass -File tests\sandbox.ps1'
    Write-Host 'or pass -Bare to run them here anyway.'
    exit 2
}

function Write-Step([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Invoke-Checked([string]$Command) {
    # Runs a native command line and stops on a non-zero exit, which PowerShell 5.1 does not do by itself.
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) { throw "Failed with exit code ${LASTEXITCODE}: $Command" }
}

if (-not $Python) { $Python = 'python' }
# Git for Windows's own bash and perl, ahead of System32, whose bash.exe starts WSL.
$gitExecutable = (Get-Command git -ErrorAction Stop).Source
$gitRoot = Split-Path -Parent (Split-Path -Parent $gitExecutable)
$env:PATH = (Join-Path $gitRoot 'bin') + ';' + (Join-Path $gitRoot 'usr\bin') + ';' + $env:PATH

Write-Step "Virtual environment in $Work\venv"
New-Item -ItemType Directory -Force $Work | Out-Null
Invoke-Checked "$Python --version"
Invoke-Checked "$Python -m venv --clear `"$Work\venv`""
$env:VIRTUAL_ENV = "$Work\venv"
$env:PATH = "$Work\venv\Scripts;" + $env:PATH
Invoke-Checked "python -m pip install --disable-pip-version-check --quiet -r `"$root\tests\requirements.txt`""

Write-Step 'Ruff'
Set-Location $root
Invoke-Checked 'ruff check .'

Write-Step 'Python suite'
Invoke-Checked "python -m pytest tests -q -rs -p no:cacheprovider --junitxml `"$Work\pytest.xml`""
Invoke-Checked "python tests\skips.py `"$Work\pytest.xml`""

Write-Step 'Gallery: packages, lint, build'
Set-Location (Join-Path $root 'gallery')
Invoke-Checked 'npm ci --no-audit --no-fund'
Invoke-Checked 'npm run lint'
Invoke-Checked 'npm run build'
# The committed assets must be the build of the committed source: no changed file, and no new one.
$assetChanges = git -C $root status --porcelain -- profile/skills/gallery/web
if ($LASTEXITCODE -ne 0) { throw "git status failed with exit code $LASTEXITCODE" }
if ($assetChanges) {
    $assetChanges | ForEach-Object { Write-Host $_ }
    throw 'The gallery build differs from the committed assets in profile/skills/gallery/web. Build and commit them.'
}

Write-Step 'Gallery: Playwright'
Invoke-Checked 'npx playwright install chromium'
Invoke-Checked 'npm test'

Write-Host ''
Write-Host 'All checks passed.' -ForegroundColor Green
