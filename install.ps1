# Installs apt-scraper on Windows: Hermes Agent, Claude Code for the Claude subscription, and this repository as
# the Hermes profile apt-scraper. Safe to run again: every step updates what is there. In PowerShell:
#   iex (irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.ps1)
$ErrorActionPreference = 'Stop'
$name = 'apt-scraper'
function Update-Path {
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User') + ';' + $env:PATH
}
function Invoke-Step([string[]]$Command) {
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "$($Command -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { iex (irm https://hermes-agent.nousresearch.com/install.ps1); Update-Path }
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { irm https://claude.ai/install.ps1 | iex; Update-Path }
if ("$(claude auth status 2>$null)" -notmatch '"loggedIn": *true') { Invoke-Step claude, 'auth', 'login' }

Invoke-Step hermes, 'profile', 'install', 'github.com/stjepanvrbic/apt-scraper', '--force', '--yes'
Invoke-Step hermes, '-p', $name, 'plugins', 'install', 'claude-subscription-directsdk', '--force', '--enable'
Invoke-Step hermes, '-p', $name, 'skills', 'install', 'google-workspace', '--yes'
Invoke-Step hermes, '-p', $name, 'computer-use', 'install', '--upgrade'
foreach ($job in 'contact', 'search', 'features', 'review', 'encoder') { Invoke-Step hermes, '-p', $name, 'cron', 'resume', "$name-$job" }
Invoke-Step hermes, 'gateway', 'install', '--if-missing', '--start-now', '--start-on-login'

Write-Host "Installed. Open the chat with: hermes -p $name dashboard" -ForegroundColor Green
