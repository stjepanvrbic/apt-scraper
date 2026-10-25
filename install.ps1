#Requires -Version 5.1
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on Windows.
# Safe to re-run: every step updates what is already there.
$ErrorActionPreference = 'Stop'

$ProfileName = 'apt-scraper'
$PluginName = 'claude-subscription-directsdk'
$MinimumClaudeVersion = [version]'2.1.284'
$MinimumHermesVersion = [version]'0.22.5'
$ProfileSource = Join-Path $PSScriptRoot 'profile'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

# Windows PowerShell turns a native command's stderr into terminating errors
# under 'Stop' once output is redirected, so native commands run under
# 'Continue' and fail on their exit code instead.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure) {
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    Invoke-Native $Exe $Arguments | Out-Host
}

function Get-Version([string]$Text) {
    [version]([regex]::Match($Text, '\d+\.\d+\.\d+').Value)
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
    $env:Path = @(
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        $env:Path
    ) -join ';'
}

Write-Step 'Claude Code'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Invoke-RemoteInstaller 'https://claude.ai/install.ps1' @()
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if ((Get-Version (Invoke-Native claude @('--version') | Out-String)) -lt $MinimumClaudeVersion) { Invoke-Checked claude @('update') }
if ((Invoke-Native claude @('auth', 'status') -AllowFailure | Out-String) -notmatch '"loggedIn":\s*true') {
    Write-Step 'Log in to Claude with your Pro or Max subscription'
    Invoke-Checked claude @('auth', 'login')
}

Write-Step 'Hermes Agent and computer-use driver'
$hermes = "$env:LOCALAPPDATA\hermes\bin\hermes.exe"
if (-not (Test-Path $hermes)) {
    Invoke-RemoteInstaller 'https://hermes-agent.nousresearch.com/install.ps1' @('-NonInteractive')
} elseif ((Get-Version (Invoke-Native $hermes @('--version') | Out-String)) -lt $MinimumHermesVersion) {
    Invoke-Checked $hermes @('update')
}

# Reinstalling from this checkout keeps working after the clone moves, which
# `hermes profile update` does not. The user's config.yaml is kept.
Write-Step "Hermes profile '$ProfileName'"
$config = "$env:LOCALAPPDATA\hermes\profiles\$ProfileName\config.yaml"
$savedConfig = [IO.Path]::GetTempFileName()
$hadConfig = Test-Path $config
if ($hadConfig) { Copy-Item $config $savedConfig -Force }
try {
    Invoke-Checked $hermes @('profile', 'install', $ProfileSource, '--force', '--yes')
} finally {
    if ($hadConfig) { Copy-Item $savedConfig $config -Force }
    Remove-Item $savedConfig
}

# The search runs curl_cffi in an environment of its own, built from hash-pinned wheels with the Python
# Hermes runs cron scripts with, which `hermes pm env` puts on PATH.
Write-Step 'Search environment'
$hermesPath = (Invoke-Native $hermes @('pm', 'env') | Out-String | ConvertFrom-Json).PATH -split ';'
$python = $hermesPath | ForEach-Object { Join-Path $_ 'python.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { throw 'Hermes reports no Python of its own in `hermes pm env`.' }
$profileHome = "$env:LOCALAPPDATA\hermes\profiles\$ProfileName"
$savedHome = $env:HERMES_HOME
$env:HERMES_HOME = $profileHome
try {
    Invoke-Checked $python @("$profileHome\scripts\search-run.py", 'install')
} finally {
    $env:HERMES_HOME = $savedHome
}

# Hermes installs a profile's shipped cron jobs paused. The listing correspondence job wakes the
# model only when a contact request or a reply needs it, and the listing search searches only once
# the interview is complete, so both stay on.
Write-Step 'Listing correspondence and listing search jobs'
Invoke-Checked $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-contact')
Invoke-Checked $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-search')

# Each listing is read once by a subagent of its own, pinned to Sonnet through delegation.model. A kept
# config.yaml gains the pin only when it names no subagent model of its own.
Write-Step 'Listing features job'
if (-not (Invoke-Native $hermes @('-p', $ProfileName, 'config', 'get', 'delegation.model') | Out-String).Trim()) {
    Invoke-Checked $hermes @('-p', $ProfileName, 'config', 'set', 'delegation.model', 'sonnet')
}
Invoke-Checked $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-features')

# The ranking review job checks every minute whether a rerank's review pass waits, and wakes the model only then.
Write-Step 'Ranking review job'
Invoke-Checked $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-review')

Write-Step 'Claude subscription provider plugin'
Invoke-Checked $hermes @('-p', $ProfileName, 'plugins', 'install', $PluginName, '--force', '--enable')

Write-Step 'Computer-use driver'
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'install', '--upgrade')
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'status')

Write-Step 'Test message through the Claude subscription'
$reply = Invoke-Native $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q') | Out-String
if ($reply -notmatch '\bready\b') { throw "Test message failed. Reply: $reply" }

Write-Host ''
Write-Host "Installed. Run: hermes -p $ProfileName" -ForegroundColor Green
