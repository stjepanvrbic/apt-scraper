# Runs every check on Windows in a fresh virtual environment: Ruff, the Python suite with zero skips, then the
# gallery's lint, build, build-output check and Playwright suite. Needs Python 3.14 and Node 24. CI runs it on a
# fresh machine.
#   tests\run.ps1 [-Work <directory>] [-Python <interpreter command>]
param(
    [string]$Work = (Join-Path $env:TEMP 'apt-scraper-tests'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Invoke-Checked([string]$Command) {
    # Runs a native command line and stops on a non-zero exit, which PowerShell 5.1 does not do by itself.
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) { throw "Failed with exit code ${LASTEXITCODE}: $Command" }
}

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
$assetChanges = git -C $root status --porcelain -- skills/gallery/web
if ($LASTEXITCODE -ne 0) { throw "git status failed with exit code $LASTEXITCODE" }
if ($assetChanges) {
    $assetChanges | ForEach-Object { Write-Host $_ }
    throw 'The gallery build differs from the committed assets in skills/gallery/web. Build and commit them.'
}

Write-Step 'Gallery: Playwright'
Invoke-Checked 'npx playwright install chromium'
Invoke-Checked 'npm test'

Write-Host ''
Write-Host 'All checks passed.' -ForegroundColor Green
