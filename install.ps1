#Requires -Version 5.1
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on Windows,
# then opens the profile's chat in the Hermes web dashboard.
# Safe to re-run: every step updates what is already there.
$ErrorActionPreference = 'Stop'

$ProfileName = 'apt-scraper'
$PluginName = 'claude-subscription-directsdk'
$MinimumClaudeVersion = [version]'2.1.284'
$MinimumHermesVersion = [version]'0.22.5'
$ProfileSource = Join-Path $PSScriptRoot 'profile'
$HermesRoot = "$env:LOCALAPPDATA\hermes"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

# Windows PowerShell gives a native command run as a statement of its own, with nothing taking its
# output, this window's console: the command shows what it prints as it prints it, a question that ends
# without a newline included, and reads the answer from the keyboard. A command whose output is piped or
# assigned prints into a pipe that PowerShell reads a line at a time, so such a question stays hidden
# while the command waits for its answer. Every command runs through Invoke-OnConsole, called as a
# statement of its own, except those whose output this script reads, which run through Get-NativeOutput.
# Windows PowerShell turns a native command's stderr into terminating errors under 'Stop' once output
# is redirected, so native commands run under 'Continue' and fail on their exit code instead.
function Invoke-OnConsole([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Get-NativeOutput([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure) {
    $ErrorActionPreference = 'Continue'
    $output = & $Exe @Arguments | Out-String
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
    $output
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
        Invoke-OnConsole $powershell (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script) + $Arguments)
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
if ((Get-Version (Get-NativeOutput claude @('--version'))) -lt $MinimumClaudeVersion) { Invoke-OnConsole claude @('update') }
if ((Get-NativeOutput claude @('auth', 'status') -AllowFailure) -notmatch '"loggedIn":\s*true') {
    Write-Step 'Log in to Claude with your Pro or Max subscription'
    Invoke-OnConsole claude @('auth', 'login')
}

Write-Step 'Hermes Agent and computer-use driver'
$hermes = "$env:LOCALAPPDATA\hermes\bin\hermes.exe"
if (-not (Test-Path $hermes)) {
    Invoke-RemoteInstaller 'https://hermes-agent.nousresearch.com/install.ps1' @('-NonInteractive')
} elseif ((Get-Version (Get-NativeOutput $hermes @('--version'))) -lt $MinimumHermesVersion) {
    Invoke-OnConsole $hermes @('update')
}

# Reinstalling from this checkout keeps working after the clone moves, which
# `hermes profile update` does not. Hermes installs a distribution only into a live profile, so a
# missing name, or one that was deleted and is marked in profiles\.deleted, is created first, without
# what the deleted profile or a failed install left behind. Only a profile this distribution installed
# before keeps the user's config.yaml; any other gets the distribution's.
Write-Step "Hermes profile '$ProfileName'"
$profileHome = "$HermesRoot\profiles\$ProfileName"
$config = "$profileHome\config.yaml"
$deleted = Test-Path "$HermesRoot\profiles\.deleted\$ProfileName"
$keepConfig = -not $deleted -and (Test-Path "$profileHome\distribution.yaml") -and (Test-Path $config)
if ($deleted -and (Test-Path $profileHome)) { Remove-Item -Recurse -Force $profileHome }
if (-not (Test-Path $profileHome)) {
    Invoke-OnConsole $hermes @('profile', 'create', $ProfileName, '--no-alias')
    Remove-Item $config -Force -ErrorAction Ignore
}
$savedConfig = [IO.Path]::GetTempFileName()
if ($keepConfig) { Copy-Item $config $savedConfig -Force }
try {
    Invoke-OnConsole $hermes @('profile', 'install', $ProfileSource, '--force', '--yes')
} finally {
    if ($keepConfig) { Copy-Item $savedConfig $config -Force }
    Remove-Item $savedConfig
}

# The account connections use Hermes's built-in google-workspace skill, which installing a profile
# from a directory does not copy.
Write-Step 'Google Workspace skill'
Invoke-OnConsole $hermes @('-p', $ProfileName, 'skills', 'install', 'google-workspace', '--yes')

# The search runs curl_cffi in an environment of its own, built from hash-pinned wheels with the Python
# Hermes runs cron scripts with, which `hermes pm env` puts on PATH.
Write-Step 'Search environment'
$hermesPath = (Get-NativeOutput $hermes @('pm', 'env') | ConvertFrom-Json).PATH -split ';'
$python = $hermesPath | ForEach-Object { Join-Path $_ 'python.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { throw 'Hermes reports no Python of its own in `hermes pm env`.' }
$savedHome = $env:HERMES_HOME
$env:HERMES_HOME = $profileHome
try {
    Invoke-OnConsole $python @("$profileHome\scripts\search-run.py", 'install')
} finally {
    $env:HERMES_HOME = $savedHome
}

# Hermes installs a profile's shipped cron jobs paused. The listing correspondence job wakes the
# model only when a contact request or a reply needs it, and the listing search searches only once
# the interview is complete, so both stay on.
Write-Step 'Listing correspondence and listing search jobs'
Invoke-OnConsole $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-contact')
Invoke-OnConsole $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-search')

# Each listing is read once by a subagent of its own, pinned to Sonnet through delegation.model. A kept
# config.yaml gains the pin only when it names no subagent model of its own.
Write-Step 'Listing features job'
if (-not (Get-NativeOutput $hermes @('-p', $ProfileName, 'config', 'get', 'delegation.model')).Trim()) {
    Invoke-OnConsole $hermes @('-p', $ProfileName, 'config', 'set', 'delegation.model', 'sonnet')
}
Invoke-OnConsole $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-features')

# The ranking review job checks every minute whether a rerank's review pass waits, and wakes the model only then.
Write-Step 'Ranking review job'
Invoke-OnConsole $hermes @('-p', $ProfileName, 'cron', 'resume', 'apt-scraper-review')

Write-Step 'Claude subscription provider plugin'
Invoke-OnConsole $hermes @('-p', $ProfileName, 'plugins', 'install', $PluginName, '--force', '--enable')

Write-Step 'Computer-use driver'
Invoke-OnConsole $hermes @('-p', $ProfileName, 'computer-use', 'install', '--upgrade')
Invoke-OnConsole $hermes @('-p', $ProfileName, 'computer-use', 'status')

# One gateway on the machine serves every profile. It runs the scheduled jobs, the messaging gateway
# and every chat turn, including the dashboard's, so it starts at login and the search keeps running
# while no chat is open. Hermes refuses gateway services for named profiles.
Write-Step 'Hermes gateway service'
Invoke-OnConsole $hermes @('-p', 'default', 'gateway', 'install', '--if-missing', '--start-now', '--start-on-login')
Invoke-OnConsole $hermes @('-p', 'default', 'gateway', 'start')

Write-Step 'Test message through the Claude subscription'
$reply = Get-NativeOutput $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q')
if ($reply -notmatch '\bready\b') { throw "Test message failed. Reply: $reply" }

# The first start builds the dashboard's web UI, so later starts need no npm. The dashboard keeps
# running after this window closes. A dashboard that is already running prints its address and exits.
# The launchers below start and open the dashboard at the address recorded here.
Write-Step 'Hermes web dashboard'
$dashboardLog = Join-Path ([IO.Path]::GetTempPath()) "hermes-dashboard-$([IO.Path]::GetRandomFileName()).log"
$dashboard = Start-Process -FilePath $hermes -ArgumentList @('-p', $ProfileName, 'dashboard', '--no-open') -WindowStyle Hidden `
    -RedirectStandardOutput $dashboardLog -RedirectStandardError "$dashboardLog.err" -PassThru
$deadline = (Get-Date).AddMinutes(15)
$dashboardUrl = $null
while (-not $dashboardUrl) {
    $exited = $dashboard.HasExited
    $output = "$(Get-Content $dashboardLog -Raw -ErrorAction SilentlyContinue)"
    if ($output -match 'HERMES_DASHBOARD_READY port=(\d+)') { $dashboardUrl = "http://127.0.0.1:$($Matches[1])" }
    elseif ($output -match "Managing profile '[^']*': (https?://[^/\s]+)") { $dashboardUrl = $Matches[1] }
    elseif ($exited -or (Get-Date) -gt $deadline) {
        throw "The Hermes dashboard did not start. Its output:`n$output$(Get-Content "$dashboardLog.err" -Raw -ErrorAction SilentlyContinue)"
    } else { Start-Sleep -Seconds 1 }
}
Set-Content -Path "$profileHome\dashboard-address" -Value $dashboardUrl -NoNewline -Encoding ASCII
$chatUrl = "$dashboardUrl/chat?profile=$ProfileName"
Start-Process $chatUrl

# The dashboard starts at login, and the Start menu's apt-scraper opens its chat, starting the dashboard
# first when Hermes does not answer at the recorded address. Any listener answers HTTP, so only
# Hermes's health reply counts: "ok" with its version, matched by a pattern because VBScript has no
# JSON parser. wscript has no console and runs the dashboard's console hidden, as Hermes's own gateway
# launcher does.
# `hermes update` rebuilds the web UI, so these starts skip the build.
Write-Step 'Dashboard at login and the apt-scraper shortcut'
function ConvertTo-VbsString([string]$Text) { '"' + $Text.Replace('"', '""') + '"' }
$launcher = "$profileHome\dashboard.vbs"
$dashboardCommand = "`"$hermes`" -p $ProfileName dashboard --no-open --skip-build --port "
Set-Content -Path $launcher -Encoding Unicode -Value @(
    "' Starts the $ProfileName dashboard without a window unless Hermes answers at its recorded address."
    "' With /open, opens its chat once Hermes answers there."
    'Option Explicit'
    'Dim shell, address, port, deadline'
    'Set shell = CreateObject("WScript.Shell")'
    "address = CreateObject(`"Scripting.FileSystemObject`").OpenTextFile($(ConvertTo-VbsString "$profileHome\dashboard-address")).ReadAll"
    'port = Mid(address, InStrRev(address, ":") + 1)'
    "If Not HermesAnswers() Then shell.Run $(ConvertTo-VbsString $dashboardCommand) & port, 0, False"
    'If WScript.Arguments.Named.Exists("open") Then'
    '    deadline = DateAdd("s", 120, Now)'
    '    Do Until HermesAnswers()'
    '        If Now > deadline Then'
    "            WScript.Echo $(ConvertTo-VbsString "The $ProfileName dashboard did not answer at ") & address & $(ConvertTo-VbsString ". Run 'hermes -p $ProfileName dashboard --port ") & port & $(ConvertTo-VbsString "' in a terminal to see why.")"
    '            WScript.Quit 1'
    '        End If'
    '        WScript.Sleep 500'
    '    Loop'
    "    shell.Run address & $(ConvertTo-VbsString "/chat?profile=$ProfileName")"
    'End If'
    ''
    'Function HermesAnswers()'
    '    Dim request, health'
    '    HermesAnswers = False'
    '    Set request = CreateObject("WinHttp.WinHttpRequest.5.1")'
    '    request.SetTimeouts 1000, 1000, 1000, 1000'
    '    On Error Resume Next'
    '    request.Open "GET", address & "/api/health", False'
    '    request.Send'
    '    If Err.Number <> 0 Then Exit Function'
    '    On Error GoTo 0'
    '    If request.Status <> 200 Then Exit Function'
    '    Set health = New RegExp'
    '    health.Pattern = "^\{(?=.*""ok"":\s*true[,}])(?=.*""version"":\s*"")"'
    '    HermesAnswers = health.Test(request.ResponseText)'
    'End Function'
)
$programs = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
foreach ($entry in @(
    @{ Path = "$programs\Startup\$ProfileName dashboard.lnk"; Arguments = "`"$launcher`""; Description = "Starts the $ProfileName dashboard" },
    @{ Path = "$programs\$ProfileName.lnk"; Arguments = "`"$launcher`" /open"; Description = "Opens the $ProfileName chat" }
)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $entry.Path) | Out-Null
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($entry.Path)
    $shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
    $shortcut.Arguments = $entry.Arguments
    $shortcut.Description = $entry.Description
    $shortcut.Save()
}

Write-Host ''
Write-Host "Installed. The Hermes dashboard is open at $chatUrl" -ForegroundColor Green
Write-Host "Say hello in its chat to start. To open it again, choose $ProfileName in the Start menu." -ForegroundColor Green
