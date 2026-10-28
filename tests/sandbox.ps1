# Runs every check, tests\run.ps1, on a fresh Windows machine: a Windows Sandbox with nothing on it but Windows,
# into which this checkout is mapped read-only. The sandbox unpacks Python, Git for Windows and Node from zip
# builds this script downloads once and checks against their pinned hashes, copies the checkout, runs the
# checks and shuts down. What the run prints shows here as it happens, and this exits with the checks' exit code.
#   tests\sandbox.ps1 [-KeepOpen] [-TimeoutMinutes 120] [-MemoryMB 8192]
# The sandbox is started through the `wsb` command, so it has an ID, and it is stopped through `wsb stop` when the
# checks end, when the timeout passes and when this script is interrupted: a sandbox that outlives its run holds
# gigabytes of memory until the machine restarts. The script refuses to start while another sandbox runs.
# -KeepOpen leaves the sandbox running after the checks, to look around with `wsb connect --id <ID>`; stop it
# yourself with `wsb stop --id <ID>`.
# Windows Sandbox is an optional feature of Windows 10 and 11 Pro, Enterprise and Education. To enable it, in an
# administrator PowerShell:
#   Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All
# then restart Windows.
param(
    [switch]$KeepOpen,
    [int]$TimeoutMinutes = 120,
    [int]$MemoryMB = 8192
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot

# What the sandbox unpacks: zip builds, not installers, because the Windows Installer service inside a sandbox
# hung on Python's core MSI. tests\sandbox-inside.ps1 names the same files.
$toolchain = @(
    @{ File = 'python.3.14.10.nupkg'; Url = 'https://api.nuget.org/v3-flatcontainer/python/3.14.10/python.3.14.10.nupkg'
       Sha256 = 'f54ba67c9951c3d773ea47d2bb095150c037c399f0423a968bcda22809241784' },
    @{ File = 'PortableGit-2.56.0.4-64-bit.7z.exe'
       Url = 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/PortableGit-2.56.0.4-64-bit.7z.exe'
       Sha256 = 'b1f3e52216e2ed6882d1bebfd4d3fdf319bec40d2edbbf9fd26ec4fd3fce7ed1' },
    @{ File = 'node-v24.22.2-win-x64.zip'; Url = 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-win-x64.zip'
       Sha256 = 'ca1bff5d8ead13c9f2ea404a176db46bd430704d5b5113734049fa365e7799e2' }
)

function Get-RunningSandboxes {
    $listing = wsb list --raw 2>$null | Out-String
    if (-not $listing.Trim()) { return @() }
    return @(($listing | ConvertFrom-Json).WindowsSandboxEnvironments | ForEach-Object { $_.Id })
}

if (-not (Get-Command wsb -ErrorAction SilentlyContinue)) {
    Write-Host 'Windows Sandbox is not enabled, or its wsb command is missing. In an administrator PowerShell, run' -ForegroundColor Red
    Write-Host '  Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All'
    Write-Host 'then restart Windows and run this again.'
    exit 1
}
$running = Get-RunningSandboxes
if ($running.Count) {
    Write-Host "A Windows Sandbox is already running, and Windows runs one at a time. Stop it first:" -ForegroundColor Red
    $running | ForEach-Object { Write-Host "  wsb stop --id $_" }
    exit 1
}
if (Get-Process -Name vmmemWindowsSandbox -ErrorAction SilentlyContinue) {
    Write-Host 'A sandbox virtual machine is running that wsb does not list, left over from a session that was killed.' -ForegroundColor Red
    Write-Host 'Only an administrator can stop it: in an administrator PowerShell, `hcsdiag list` shows it with the owner'
    Write-Host 'WindowsSandbox, and `hcsdiag kill <ID>` stops that one. Leave the WSL and CmService entries alone.'
    exit 1
}

$base = Join-Path $env:LOCALAPPDATA 'apt-scraper\sandbox'
$downloads = Join-Path $base 'toolchain'
# Package caches the sandbox fills and reuses: pip's wheels, npm's packages and Playwright's browsers. Each is
# keyed by content, so a kept cache changes nothing about what a run installs.
$cache = Join-Path $base 'cache'
$run = Join-Path $base ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$out = Join-Path $run 'out'
foreach ($directory in $downloads, $cache, $out) { New-Item -ItemType Directory -Force $directory | Out-Null }

Write-Host '==> Toolchain installers' -ForegroundColor Cyan
foreach ($tool in $toolchain) {
    $path = Join-Path $downloads $tool.File
    if ((Test-Path $path) -and ((Get-FileHash $path -Algorithm SHA256).Hash -eq $tool.Sha256)) {
        Write-Host "    $($tool.File): kept"
        continue
    }
    Write-Host "    $($tool.File): downloading"
    $partial = "$path.partial"
    Invoke-WebRequest -Uri $tool.Url -OutFile $partial -UseBasicParsing
    $hash = (Get-FileHash $partial -Algorithm SHA256).Hash
    if ($hash -ne $tool.Sha256) {
        Remove-Item $partial
        throw "$($tool.File) from $($tool.Url) has SHA-256 $hash, not the pinned $($tool.Sha256)."
    }
    Move-Item -Force $partial $path
}

@{ keepOpen = [bool]$KeepOpen } | ConvertTo-Json | Set-Content -Encoding ascii (Join-Path $out 'options.json')
$desktop = 'C:\Users\WDAGUtilityAccount\Desktop'
function Get-MappedFolder([string]$HostFolder, [string]$Name, [bool]$ReadOnly) {
    $escaped = [System.Security.SecurityElement]::Escape($HostFolder)
    "<MappedFolder><HostFolder>$escaped</HostFolder><SandboxFolder>$desktop\$Name</SandboxFolder>" +
    "<ReadOnly>$($ReadOnly.ToString().ToLower())</ReadOnly></MappedFolder>"
}
$configuration = @(
    '<Configuration>',
    "<MemoryInMB>$MemoryMB</MemoryInMB>",
    '<MappedFolders>',
    (Get-MappedFolder $root 'apt-scraper' $true),
    (Get-MappedFolder $downloads 'toolchain' $true),
    (Get-MappedFolder $cache 'cache' $false),
    (Get-MappedFolder $out 'out' $false),
    '</MappedFolders>',
    '</Configuration>'
) -join ''
Set-Content -Encoding ascii (Join-Path $run 'checks.wsb') $configuration

$log = Join-Path $out 'log.txt'
$exitFile = Join-Path $out 'exit-code'
Write-Host "==> Starting Windows Sandbox; its log is $log" -ForegroundColor Cyan
$started = wsb start --config $configuration --raw 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or -not $started.Trim().StartsWith('{')) { throw "wsb start failed: $started" }
$id = ($started | ConvertFrom-Json).Id
if (-not $id) { throw "wsb start returned no sandbox ID: $started" }
Write-Host "    sandbox $id"
# A sandbox started this way has no logged-on user, so the guest script runs through `wsb exec` as System, in a
# process of its own that this script does not wait on: the exit-code file says when the run is over.
$guest = "powershell -NoProfile -ExecutionPolicy Bypass -File $desktop\apt-scraper\tests\sandbox-inside.ps1"
$null = Start-Process -NoNewWindow -FilePath (Get-Command wsb).Source `
    -ArgumentList 'exec', '--id', $id, '--run-as', 'System', '-c', ('"' + $guest + '"')

$script:shown = 0
function Show-NewLines {
    if (-not (Test-Path $log)) { return }
    $lines = @(Get-Content $log -ErrorAction SilentlyContinue)
    if ($lines.Count -gt $script:shown) {
        $lines[$script:shown..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
        $script:shown = $lines.Count
    }
}
$code = 1
$timedOut = $false
try {
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while (-not (Test-Path $exitFile)) {
        if ((Get-Date) -gt $deadline) { $timedOut = $true; break }
        Start-Sleep -Seconds 5
        Show-NewLines
    }
    Start-Sleep -Seconds 2
    Show-NewLines
    if (-not $timedOut) { $code = [int](Get-Content $exitFile | Select-Object -First 1) }
} finally {
    if ($KeepOpen -and -not $timedOut) {
        Write-Host "==> Sandbox $id is left running. Look around with 'wsb connect --id $id'; stop it with 'wsb stop --id $id'." -ForegroundColor Yellow
    } else {
        wsb stop --id $id 2>$null | Out-Null
        if ((Get-RunningSandboxes) -contains $id) {
            Write-Host "==> Sandbox $id did not stop. Stop it with 'wsb stop --id $id'." -ForegroundColor Red
        } else {
            Write-Host "==> Sandbox $id stopped."
        }
    }
}
if ($timedOut) {
    Write-Host "==> The sandbox did not finish within $TimeoutMinutes minutes. Its log is $log." -ForegroundColor Red
    exit 1
}
if ($code -eq 0) { Write-Host "==> The checks passed in the sandbox. Log: $log" -ForegroundColor Green }
else { Write-Host "==> The checks failed in the sandbox with exit code $code. Log: $log" -ForegroundColor Red }
exit $code
