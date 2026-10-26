# Installs Git when it is missing, clones this repository into the user's local application data or
# updates the clone there, and runs the installer from the clone with this script's arguments.
# Written for:
#   irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/bootstrap.ps1 | iex
# Invoke-Expression runs it in the caller's session, so its settings stay inside the script block and
# it fails by throwing, because exit would close the caller's window.
& {
    $ErrorActionPreference = 'Stop'
    $RepositoryUrl = 'https://github.com/stjepanvrbic/apt-scraper.git'
    $Repository = Join-Path $env:LOCALAPPDATA 'apt-scraper\repo'

    function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

    # Windows PowerShell turns a native command's stderr into terminating errors under 'Stop' once
    # output is redirected, so native commands run under 'Continue' and fail on their exit code instead.
    function Invoke-Native([string]$Exe, [string[]]$Arguments) {
        $ErrorActionPreference = 'Continue'
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Step 'Git'
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Git is missing, and winget, which installs it, is too. Install Git from https://git-scm.com/download/win and run this again.'
        }
        Invoke-Native winget @('install', '--id', 'Git.Git', '--exact', '--source', 'winget',
            '--accept-package-agreements', '--accept-source-agreements')
        # The Git installer adds itself to the PATH in the registry, which this session read before.
        $env:Path = @(
            $env:Path,
            [Environment]::GetEnvironmentVariable('Path', 'Machine'),
            [Environment]::GetEnvironmentVariable('Path', 'User')
        ) -join ';'
    }

    if (Test-Path -LiteralPath (Join-Path $Repository '.git')) {
        Write-Step "Updating $Repository"
        Invoke-Native git @('-C', $Repository, 'pull', '--ff-only')
    } else {
        Write-Step "Cloning apt-scraper into $Repository"
        Invoke-Native git @('clone', $RepositoryUrl, $Repository)
    }

    # The installer is written for and tested with Windows PowerShell, so it runs there whichever shell the line
    # was pasted into.
    $powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    Invoke-Native $powershell (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Repository 'install.ps1')) + $args)
} @args
