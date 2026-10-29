# Installs apt-scraper on Windows: Hermes Agent, Claude Code for the Claude subscription, and this repository as
# the Hermes profile apt-scraper. Safe to run again: every step updates what is there. In PowerShell:
#   iex (irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.ps1)
# -Source names another profile source for `hermes profile install`, such as a clone of this repository.
param([string]$Source = 'github.com/stjepanvrbic/apt-scraper')
$ErrorActionPreference = 'Stop'
$name = 'apt-scraper'
function Update-Path {
    # Where Hermes's and Claude Code's installers put their commands. Claude Code's leaves adding its directory to
    # PATH to the user, so this adds it to the user's PATH once, for later terminals, and to this session's.
    # The user's PATH is read and written through the registry, unexpanded, so entries such as %JAVA_HOME%\bin stay as they are.
    $claudeBin = "$env:USERPROFILE\.local\bin"
    $environment = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    $userPath = [string]$environment.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    if (($userPath -split ';') -notcontains $claudeBin) {
        $environment.SetValue('Path', "$claudeBin;$userPath".TrimEnd(';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    }
    $environment.Close()
    $env:PATH = "$env:LOCALAPPDATA\hermes\bin;$claudeBin;" + [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User') + ';' + $env:PATH
}
function Invoke-Step([string[]]$Command) {
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "$($Command -join ' ') failed with exit code $LASTEXITCODE" }
}

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { & ([scriptblock]::Create((irm https://hermes-agent.nousresearch.com/install.ps1))) -NonInteractive; Update-Path }
# The Claude subscription provider plugin runs on the official Claude Code CLI, installed and logged in.
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { irm https://claude.ai/install.ps1 | iex; Update-Path }
if ("$(claude auth status 2>$null)" -notmatch '"loggedIn": *true') {
    if ([Console]::IsInputRedirected) { Write-Host 'No terminal to log in to Claude on. Run: claude auth login' } else { Invoke-Step claude, 'auth', 'login' }
}

Invoke-Step hermes, 'profile', 'install', $Source, '--force', '--yes'
Invoke-Step hermes, '-p', $name, 'plugins', 'install', 'claude-subscription-directsdk', '--force', '--enable'
Invoke-Step hermes, '-p', $name, 'skills', 'install', 'google-workspace', '--yes'
Invoke-Step hermes, '-p', $name, 'computer-use', 'install', '--upgrade'
Invoke-Step hermes, '-p', $name, 'cron', 'resume', 'apt-scraper-contact'
Invoke-Step hermes, '-p', $name, 'cron', 'resume', 'apt-scraper-search'
Invoke-Step hermes, '-p', $name, 'cron', 'resume', 'apt-scraper-features'
Invoke-Step hermes, '-p', $name, 'cron', 'resume', 'apt-scraper-review'
Invoke-Step hermes, '-p', $name, 'cron', 'resume', 'apt-scraper-encoder'
Invoke-Step hermes, 'gateway', 'install', '--if-missing', '--start-now', '--start-on-login'

Write-Host "Installed. Open the chat with: hermes -p $name dashboard" -ForegroundColor Green
