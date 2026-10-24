#Requires -Version 5.1
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on Windows.
# Safe to re-run: every step updates what is already there.
$ErrorActionPreference = 'Stop'

$ProfileName = 'apt-scraper'
$PluginName = 'claude-subscription-directsdk'
$MinimumHermesVersion = [version]'0.22.5'
$ProfileSource = Join-Path $PSScriptRoot 'profile'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

# Windows PowerShell turns a native command's stderr into terminating errors
# under 'Stop', so commands whose stderr is discarded run under 'Continue'.
function Invoke-StdoutOnly([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    (& $Exe @Arguments 2>$null) | Out-String
}

function Update-SessionPath {
    $env:Path = @(
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        $env:Path
    ) -join ';'
}

# Upstream installers are written for `irm | iex` and may call `exit`, so each
# runs in its own PowerShell process instead of this one.
function Invoke-RemoteInstaller([string]$Url, [string[]]$Arguments) {
    $directory = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $directory | Out-Null
    try {
        $script = Join-Path $directory 'install.ps1'
        Invoke-WebRequest -Uri $Url -OutFile $script -UseBasicParsing
        $powershell = (Get-Process -Id $PID).Path
        Invoke-Checked $powershell (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script) + $Arguments)
    } finally {
        Remove-Item -Recurse -Force $directory
    }
    Update-SessionPath
}

Write-Step 'Claude Code'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Invoke-RemoteInstaller 'https://claude.ai/install.ps1' @()
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Invoke-Checked claude @('--version')

$auth = Invoke-StdoutOnly claude @('auth', 'status') | ConvertFrom-Json
if (-not $auth.loggedIn) {
    Write-Step 'Log in to Claude with your Pro or Max subscription'
    Invoke-Checked claude @('auth', 'login')
}

Write-Step 'Hermes Agent and computer-use driver'
$hermes = "$env:LOCALAPPDATA\hermes\bin\hermes.exe"
if (-not (Test-Path $hermes)) {
    Invoke-RemoteInstaller 'https://hermes-agent.nousresearch.com/install.ps1' @('-NonInteractive', '-SkipSetup')
} else {
    $installed = [version](((& $hermes --version) | Select-Object -First 1) -replace '^Hermes Agent v(\d+\.\d+\.\d+).*$', '$1')
    if ($installed -lt $MinimumHermesVersion) { Invoke-Checked $hermes @('update') }
}

Write-Step "Hermes profile '$ProfileName'"
Invoke-StdoutOnly $hermes @('profile', 'show', $ProfileName) | Out-Null
if ($LASTEXITCODE -eq 0) {
    Invoke-Checked $hermes @('profile', 'update', $ProfileName, '--yes')
} else {
    Invoke-Checked $hermes @('profile', 'install', $ProfileSource, '--alias', '--yes')
}

Write-Step 'Claude subscription provider plugin'
Invoke-Checked $hermes @('-p', $ProfileName, 'plugins', 'install', $PluginName, '--force', '--enable')

Write-Step 'Computer-use driver registration (Windows may ask for administrator approval)'
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'install', '--upgrade')
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'status')

Write-Step 'Smoke test: one turn through the Claude subscription'
$reply = Invoke-StdoutOnly $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q')
if ($LASTEXITCODE -ne 0 -or $reply.Trim() -ne 'ready') {
    throw "Smoke test failed (exit code $LASTEXITCODE). Reply: $reply"
}

Write-Host ''
Write-Host "Installed. Open a new terminal and run: $ProfileName" -ForegroundColor Green
