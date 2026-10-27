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
# Get-NativeOutput returns what the command printed on stdout and shows its stderr. When the command
# fails, the error carries both, since nothing else showed what it printed on stdout.
function Invoke-OnConsole([string]$Exe, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Get-NativeOutput([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure) {
    $ErrorActionPreference = 'Continue'
    $printed = & $Exe @Arguments 2>&1
    $output = $printed | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] } | Out-String
    $errors = $printed | Where-Object { $_ -is [Management.Automation.ErrorRecord] } | ForEach-Object { "$_" } | Out-String
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE. It printed:`n$output$errors"
    }
    if ($errors.Trim()) { Write-Host $errors.TrimEnd() }
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

# The account setup and the contact forms use Hermes's built-in browser tools, which Hermes replaces
# with browser_exec while browser.backend is unset. A kept config.yaml gains the setting only when it
# names no backend of its own.
Write-Step 'Browser tools'
if (-not (Get-NativeOutput $hermes @('-p', $ProfileName, 'config', 'get', 'browser.backend')).Trim()) {
    Invoke-OnConsole $hermes @('-p', $ProfileName, 'config', 'set', 'browser.backend', 'off')
}
# The account setup of an earlier version turned on real-profile browsing while it ran. One cut off by
# this upgrade would give the contact forms a copy of the user's own browser profile.
foreach ($key in 'browser.use_real_profile', 'browser.headed', 'browser.real_profile_autoclose') {
    if ((Get-NativeOutput $hermes @('-p', $ProfileName, 'config', 'get', $key)).Trim() -eq 'true') {
        Invoke-OnConsole $hermes @('-p', $ProfileName, 'config', 'set', $key, 'false')
    }
}

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
# Installing the login entry and starting the gateway are two commands. Run without administrator
# rights, `gateway install` asks in the terminal whether to open the UAC prompt for a Scheduled Task,
# and on yes hands the install to an elevated copy of Hermes. The UAC prompt is that question, so the
# installer runs `gateway install` elevated itself, hidden, as Hermes's _launch_elevated_gateway_command
# runs its elevated commands: Hermes then asks nothing and adds the Scheduled Task. Nothing starts the
# gateway from there: an install given --start-now starts it as administrator, where no chat or dashboard
# of this user can reach it. The gateway is started by this unelevated `gateway start` once the entry
# exists. A machine that has the entry skips the prompt.
Write-Step 'Hermes gateway service'
$loginEntry = 'Scheduled Task registered|Windows login item installed'
if ((Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure) -notmatch $loginEntry) {
    Write-Host 'Windows asks for administrator approval to add the gateway to the Scheduled Tasks, so it starts at login.'
    try {
        $installer = Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'install', '--if-missing', '--no-start-now', '--start-on-login') -Verb RunAs `
            -WindowStyle Hidden -PassThru -Wait -ErrorAction Stop
    } catch {
        throw ('The gateway cannot start at login without the Scheduled Task, and adding it needs administrator ' +
            "approval, which was not given ($($_.Exception.Message)). Run the install again and approve the prompt.")
    }
    if ($installer.ExitCode -ne 0) {
        throw "The administrator 'hermes gateway install' failed with exit code $($installer.ExitCode)."
    }
    $gatewayStatus = Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure
    if ($gatewayStatus -notmatch $loginEntry) {
        throw ("The gateway's login entry is missing after the administrator 'hermes gateway install'. " +
            "'hermes -p default gateway status' printed:`n$gatewayStatus")
    }
}
Invoke-OnConsole $hermes @('-p', 'default', 'gateway', 'start')

# A gateway started as administrator, by an earlier install or by hand, keeps running where this
# user's chats, dashboard and test message cannot reach it, and `gateway start` reports it as running.
# `gateway ensure` names that state, as Hermes's own clients see it: gateway_elevated, or
# gateway_other_session for one in another Windows logon session. Only an administrator can stop it,
# and Hermes's `gateway stop` offers no UAC prompt, so the installer runs `gateway stop` elevated
# through one, hidden like the install above. There `gateway stop` cannot ask before it interrupts live sessions, so it
# gets --yes, and the user is told before the prompt. Once that stop has exited and `gateway status`
# shows no gateway, the gateway is started again, unelevated. Of Hermes's text, the installer shows
# the sentence that names the gateway, not the remedy, which sends the user to an administrator prompt.
function Get-GatewayRefusal {
    $gateway = Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'ensure', '--timeout', '60') -AllowFailure | ConvertFrom-Json
    if ($gateway.reason_code -in @('gateway_elevated', 'gateway_other_session')) {
        if ($gateway.detail -match '^.*?cannot reach it\.') { $Matches[0] } else { "$($gateway.detail)." }
    }
}
$refusal = Get-GatewayRefusal
if ($refusal) {
    Write-Host $refusal
    Write-Host ('Windows asks for administrator approval to stop it. Approving stops it, interrupting any ' +
        'work it is doing, and the install starts it again without administrator rights.')
    try {
        $stopper = Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'stop', '--yes') -Verb RunAs `
            -WindowStyle Hidden -PassThru -ErrorAction Stop
    } catch {
        throw ("$refusal`nThe install cannot continue while it runs: the test message, the dashboard's chat and " +
            'the search all go through the gateway, and nothing started without administrator rights can reach ' +
            "this one. Stopping it needs administrator approval, which was not given ($($_.Exception.Message)). " +
            'Run the install again and approve the prompt.')
    }
    $stopDeadline = (Get-Date).AddMinutes(2)
    while (-not $stopper.HasExited -or
           (Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure) -match 'Gateway (process|is) running') {
        if ((Get-Date) -gt $stopDeadline) {
            $exit = if ($stopper.HasExited) { "it exited with code $($stopper.ExitCode)" } else { 'it is still running' }
            throw "$refusal`nThe administrator 'hermes gateway stop' did not stop it within 2 minutes: $exit."
        }
        Start-Sleep -Seconds 1
    }
    Invoke-OnConsole $hermes @('-p', 'default', 'gateway', 'start')
    $refusal = Get-GatewayRefusal
    if ($refusal) { throw "$refusal`nThe gateway still runs as administrator after it was stopped and started again." }
}

Write-Step 'Test message through the Claude subscription'
$reply = Get-NativeOutput $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q')
if ($reply -notmatch '\bready\b') { throw "Test message failed. Reply: $reply" }

# The first start builds the dashboard's web UI, so later starts need no npm. The dashboard keeps
# running after this window closes. A dashboard that is already running prints its address and exits.
# The launchers below start and open the dashboard at the address recorded here. The uninstaller removes
# the dashboard's log by its name.
Write-Step 'Hermes web dashboard'
$dashboardLog = Join-Path ([IO.Path]::GetTempPath()) "apt-scraper-dashboard-$([IO.Path]::GetRandomFileName()).log"
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
