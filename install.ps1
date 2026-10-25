#Requires -Version 5.1
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on Windows,
# and stores a Claude setup token for the profile.
# Safe to re-run: every step updates what is already there.
$ErrorActionPreference = 'Stop'

$ProfileName = 'apt-scraper'
$PluginName = 'claude-subscription-directsdk'
$MinimumClaudeVersion = [version]'2.1.284'
$MinimumHermesVersion = [version]'0.22.5'
$ProfileSource = Join-Path $PSScriptRoot 'profile'
$ProfileHome = "$env:LOCALAPPDATA\hermes\profiles\$ProfileName"
$TokenLine = '^\s*(export\s+)?CLAUDE_CODE_OAUTH_TOKEN\s*='

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

# Returns a token from `claude setup-token`, or an empty string to keep the
# saved one.
function Read-SetupToken([bool]$HasSavedToken) {
    $prompt = if ($HasSavedToken) { 'Setup token (Enter keeps the saved one)' } else { 'Setup token' }
    while ($true) {
        $secure = Read-Host -Prompt $prompt -AsSecureString
        $token = [Net.NetworkCredential]::new('', $secure).Password -replace '\s', ''
        if ($token -cmatch '^sk-ant-oat\d+-[A-Za-z0-9_-]+$' -or ($HasSavedToken -and -not $token)) { return $token }
        Write-Host 'That is not a token from `claude setup-token`. Paste the whole line it prints.' -ForegroundColor Yellow
    }
}

# Written here rather than with `hermes config set`, which would put the token
# on a command line.
function Save-SetupToken([string]$EnvFile, [string]$Token) {
    $utf8 = [Text.UTF8Encoding]::new($false)
    $lines = if (Test-Path $EnvFile) { [IO.File]::ReadAllLines($EnvFile, $utf8) | Where-Object { $_ -notmatch $TokenLine } } else { @() }
    $text = (@($lines) + "CLAUDE_CODE_OAUTH_TOKEN=$Token" | ForEach-Object { "$_`n" }) -join ''
    $temporary = "$EnvFile.tmp"
    [IO.File]::WriteAllText($temporary, $text, $utf8)
    Move-Item -Force $temporary $EnvFile
}

# Windows PowerShell turns a native command's stderr into terminating errors
# under 'Stop' once output is redirected, so native commands run under
# 'Continue' and fail on their exit code instead.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
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
$config = Join-Path $ProfileHome 'config.yaml'
$savedConfig = [IO.Path]::GetTempFileName()
$hadConfig = Test-Path $config
if ($hadConfig) { Copy-Item $config $savedConfig -Force }
try {
    Invoke-Checked $hermes @('profile', 'install', $ProfileSource, '--force', '--yes')
} finally {
    if ($hadConfig) { Copy-Item $savedConfig $config -Force }
    Remove-Item $savedConfig
}

# Claude Code prefers this token over a `claude auth login` session.
Write-Step 'Claude setup token'
$envFile = Join-Path $ProfileHome '.env'
$hasSavedToken = (Test-Path $envFile) -and [bool](Select-String -Path $envFile -Pattern "$TokenLine\S" -Quiet)
Write-Host 'Run `claude setup-token` in another terminal, approve access in the browser, and paste the token it prints.'
$token = Read-SetupToken $hasSavedToken
if ($token) { Save-SetupToken $envFile $token }

Write-Step 'Claude subscription provider plugin'
Invoke-Checked $hermes @('-p', $ProfileName, 'plugins', 'install', $PluginName, '--force', '--enable')

Write-Step 'Computer-use driver'
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'install', '--upgrade')
Invoke-Checked $hermes @('-p', $ProfileName, 'computer-use', 'status')

Write-Step 'Test message through the Claude subscription'
$reply = Invoke-Native $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q') | Out-String
if ($reply -notmatch '\bready\b') { throw "Test message failed. If the setup token is wrong or expired, run the installer again and paste a new one. Reply: $reply" }

Write-Host ''
Write-Host "Installed. Run: hermes -p $ProfileName" -ForegroundColor Green
