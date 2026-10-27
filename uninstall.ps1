# Removes everything apt-scraper put on this machine: the dashboard and the account setup's browser while
# they run, the dashboard's login entry and the Start menu shortcut, the Hermes profile apt-scraper and its
# deletion mark, apt-scraper's folder in the local application data with the listing store and the clone,
# and the gallery's saved connections in the user's .secrets folder. Hermes, its gateway service and Claude Code stay unless given -Everything, which also removes the
# gateway service and Hermes. Claude Code always stays, because it holds the user's own login.
# Needs no clone and asks nothing; whatever is already gone is skipped, so it is safe to run twice.
# Written for:
#   irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.ps1 | iex
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.ps1))) -Everything
# Invoke-Expression runs it in the caller's session, so its settings stay inside the script block and
# it fails by throwing, because exit would close the caller's window.
& {
    $ErrorActionPreference = 'Stop'
    $ProfileName = 'apt-scraper'
    $HermesRoot = "$env:LOCALAPPDATA\hermes"
    $ProfileHome = "$HermesRoot\profiles\$ProfileName"
    $DeletionMark = "$HermesRoot\profiles\.deleted\$ProfileName"
    $DataDirectory = "$env:LOCALAPPDATA\apt-scraper"
    $Programs = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
    $Shortcuts = @("$Programs\Startup\$ProfileName dashboard.lnk", "$Programs\$ProfileName.lnk")
    $DashboardLogs = Join-Path ([IO.Path]::GetTempPath()) 'apt-scraper-dashboard-*'
    $Secrets = "$env:USERPROFILE\.secrets\apt-scraper"
    $Line = 'https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.ps1'

    $everything = $false
    foreach ($argument in $args) {
        if ($argument -eq '-Everything') { $everything = $true }
        else { throw "Unknown argument '$argument'. The only argument is -Everything." }
    }

    function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
    function Write-Problem([string]$Message) { Write-Host $Message -ForegroundColor Yellow }

    # Windows PowerShell turns a native command's stderr into terminating errors under 'Stop' once output
    # is redirected, so native commands run under 'Continue'. Run as a statement of its own, the command
    # prints on this console. A failure is reported and the removal goes on.
    function Invoke-Hermes([string[]]$Arguments) {
        $ErrorActionPreference = 'Continue'
        & $hermes @Arguments
        if ($LASTEXITCODE -ne 0) { Write-Problem "'hermes $($Arguments -join ' ')' failed with exit code $LASTEXITCODE." }
    }

    # Windows releases a stopped process's files a moment after it exits.
    function Remove-Path([string]$Path) {
        for ($attempt = 1; Test-Path -LiteralPath $Path; $attempt++) {
            try {
                Remove-Item -LiteralPath $Path -Recurse -Force
            } catch {
                if ($attempt -ge 10) { throw "Could not remove $Path ($($_.Exception.Message)). Run this again once nothing uses it." }
                Start-Sleep -Seconds 1
            }
        }
    }

    # A command line's arguments, with Windows's quotes taken out.
    function Split-CommandLine([string]$CommandLine) {
        @([regex]::Matches($CommandLine, '(?:[^\s"]|"[^"]*")+') | ForEach-Object { $_.Value.Replace('"', '') })
    }

    # Only what apt-scraper starts: the account setup's browser on its user-data directory in the profile, or on
    # the profile copy Hermes made there before; wscript or cscript on the profile's dashboard.vbs; the gallery
    # server, whose store would keep the data folder from being removed; and hermes from this Hermes root with
    # the profile's name, which is the dashboard. Comparisons ignore case, as Windows paths do.
    function Test-ProfileProcess($Process) {
        $arguments = Split-CommandLine $Process.CommandLine
        $browser = "--user-data-dir=$ProfileHome\apt-scraper\browser"
        $copiedBrowser = "--user-data-dir=$ProfileHome\browser-profile\"
        if ($arguments | Where-Object { $_ -eq $browser -or $_.StartsWith($copiedBrowser, [StringComparison]::OrdinalIgnoreCase) }) {
            return $true
        }
        if ($Process.Name -in @('wscript.exe', 'cscript.exe') -and $arguments -contains "$ProfileHome\dashboard.vbs") { return $true }
        $galleries = @("$ProfileHome\skills\gallery\scripts\gallery.py", "$DataDirectory\repo\profile\skills\gallery\scripts\gallery.py")
        if (($arguments | Where-Object { $_ -in $galleries }) -and $arguments -contains 'serve') { return $true }
        if (-not ($arguments | Select-Object -First 2 | Where-Object { $_.StartsWith("$HermesRoot\", [StringComparison]::OrdinalIgnoreCase) })) {
            return $false
        }
        for ($index = 0; $index -lt $arguments.Count; $index++) {
            if ($arguments[$index] -eq "--profile=$ProfileName") { return $true }
            if ($arguments[$index] -in @('-p', '--profile') -and $index + 1 -lt $arguments.Count -and $arguments[$index + 1] -eq $ProfileName) {
                return $true
            }
        }
        $false
    }

    function Stop-ProfileProcesses {
        $processes = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and (Test-ProfileProcess $_)
        })
        foreach ($process in $processes) { Stop-Process -Id $process.ProcessId -Force -ErrorAction Ignore }
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline -and @($processes | Where-Object { Get-Process -Id $_.ProcessId -ErrorAction Ignore }).Count) {
            Start-Sleep -Milliseconds 200
        }
    }

    $hermes = "$HermesRoot\bin\hermes.exe"
    if (-not (Test-Path -LiteralPath $hermes)) {
        $hermes = (Get-Command hermes -CommandType Application -ErrorAction Ignore | Select-Object -First 1).Source
    }

    Write-Host 'This removes apt-scraper from this computer:'
    Write-Host '  - the apt-scraper dashboard, the gallery and the account setup''s browser window, if they run,'
    Write-Host '  - the dashboard''s login entry and the apt-scraper shortcut in the Start menu,'
    Write-Host "  - the Hermes profile $ProfileName with its settings, scheduled jobs, Google token, connection results, setup browser profile and Python environment ($ProfileHome),"
    Write-Host "  - the apt-scraper folder with the listing store and the downloaded copy of apt-scraper ($DataDirectory),"
    Write-Host "  - the gallery's saved connections ($Secrets)."
    if ($everything) {
        Write-Host 'It also removes the Hermes gateway service and Hermes. Windows may ask for administrator approval to remove the gateway''s Scheduled Task.'
        Write-Host 'Hermes keeps its own settings and data. Claude Code stays, because it holds your Claude login.'
    } else {
        Write-Host 'Hermes, the Hermes gateway service and Claude Code stay.'
    }

    # Hermes asks nothing then, and takes the answer it would default to.
    $savedNonInteractive = $env:HERMES_NONINTERACTIVE
    $env:HERMES_NONINTERACTIVE = '1'
    try {
        Write-Step 'Dashboard login entry and apt-scraper shortcut'
        foreach ($shortcut in $Shortcuts) { Remove-Path $shortcut }

        Write-Step 'Dashboard, gallery and setup browser'
        Stop-ProfileProcesses

        # `hermes profile delete` stops the profile's backends, releases what the gateway holds in it and
        # removes it, leaving a deletion mark in profiles\.deleted. The mark goes too, so a later install
        # is a fresh one.
        Write-Step "Hermes profile '$ProfileName'"
        if ($hermes -and (Test-Path -LiteralPath $ProfileHome) -and -not (Test-Path -LiteralPath $DeletionMark)) {
            Invoke-Hermes @('profile', 'delete', $ProfileName, '--yes')
        }
        Remove-Path $ProfileHome
        Remove-Path $DeletionMark

        Write-Step "apt-scraper folder $DataDirectory"
        $location = (Get-Location).ProviderPath
        if ($location -and ($location -eq $DataDirectory -or $location.StartsWith("$DataDirectory\", [StringComparison]::OrdinalIgnoreCase))) {
            Set-Location $env:USERPROFILE
        }
        Remove-Path $DataDirectory
        Get-ChildItem -Path $DashboardLogs -ErrorAction Ignore | ForEach-Object { Remove-Path $_.FullName }

        # The gallery keeps its address and access token there each time it opens. The .secrets folder is the
        # user's and stays.
        Write-Step "Gallery connections $Secrets"
        Remove-Path $Secrets

        if ($everything -and $hermes) {
            # The installer added the gateway's Scheduled Task as administrator, and only an administrator
            # removes it, so `gateway uninstall` runs elevated, hidden, as the installer ran `gateway install`.
            Write-Step 'Hermes gateway service'
            $ErrorActionPreference = 'Continue'
            $gatewayStatus = & $hermes @('-p', 'default', 'gateway', 'status') 2>&1 | Out-String
            $ErrorActionPreference = 'Stop'
            if ($gatewayStatus -match 'Scheduled Task registered') {
                Write-Host 'Windows asks for administrator approval to remove the gateway''s Scheduled Task.'
                try {
                    $uninstaller = Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'uninstall') -Verb RunAs `
                        -WindowStyle Hidden -PassThru -Wait -ErrorAction Stop
                } catch {
                    throw ("apt-scraper is removed. The gateway's Scheduled Task stays, because removing it needs administrator " +
                        "approval, which was not given ($($_.Exception.Message)), and Hermes stays with it. Run this again " +
                        'with -Everything and approve the prompt.')
                }
                if ($uninstaller.ExitCode -ne 0) {
                    Write-Problem "The administrator 'hermes gateway uninstall' failed with exit code $($uninstaller.ExitCode)."
                }
            } else {
                Invoke-Hermes @('-p', 'default', 'gateway', 'uninstall')
            }

            Write-Step 'Hermes'
            Invoke-Hermes @('uninstall', '--yes')
        }
    } finally {
        $env:HERMES_NONINTERACTIVE = $savedNonInteractive
    }

    Write-Host ''
    Write-Host 'apt-scraper is removed.' -ForegroundColor Green
    if (-not $everything) {
        Write-Host 'Hermes, its gateway service and Claude Code stay. To remove the Hermes gateway service and Hermes:'
        Write-Host '  hermes gateway uninstall'
        Write-Host '  hermes uninstall'
        Write-Host 'or run this line again with -Everything:'
        Write-Host "  & ([scriptblock]::Create((irm $Line))) -Everything"
    } elseif ($hermes) {
        Write-Host "Hermes kept its settings and data in $HermesRoot. Delete that folder to remove them too."
    }
    Write-Host 'To remove Claude Code, which holds your Claude login, follow https://code.claude.com/docs/en/setup#uninstall-claude-code'
    Write-Host '  Remove-Item -Path "$env:USERPROFILE\.local\bin\claude.exe" -Force'
    Write-Host '  Remove-Item -Path "$env:USERPROFILE\.local\share\claude" -Recurse -Force'
} @args
