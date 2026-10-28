# Runs every check, tests\run.ps1, on a fresh Windows machine: a Windows Sandbox with nothing on it but Windows,
# into which this checkout is mapped read-only. The sandbox installs Python, Git for Windows and Node from
# installers this script downloads once and checks against their pinned hashes, copies the checkout, runs the
# checks and shuts down. What the run prints shows here as it happens, and this exits with the checks' exit code.
#   tests\sandbox.ps1 [-KeepOpen] [-TimeoutMinutes 120] [-MemoryMB 8192]
# -KeepOpen leaves the sandbox open after the checks, to look around; close its window to end it.
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

# What the sandbox installs. tests\sandbox-inside.ps1 names the same files.
$toolchain = @(
    @{ File = 'python-3.14.10-amd64.exe'; Url = 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe'
       Sha256 = '92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965' },
    @{ File = 'Git-2.56.0.4-64-bit.exe'
       Url = 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/Git-2.56.0.4-64-bit.exe'
       Sha256 = '4961a4a8cc778d8b6e00a5819e5fe4d078b754555aff7aedc2d77a5d74c2de80' },
    @{ File = 'node-v24.22.2-x64.msi'; Url = 'https://nodejs.org/dist/v24.22.2/node-v24.22.2-x64.msi'
       Sha256 = '9231bf64d19bef391dd5ee8af65411d3799c3c44a842d2320849cc1da60b6b6d' }
)

$sandbox = Join-Path $env:windir 'System32\WindowsSandbox.exe'
if (-not (Test-Path $sandbox)) {
    Write-Host 'Windows Sandbox is not enabled. In an administrator PowerShell, run' -ForegroundColor Red
    Write-Host '  Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All'
    Write-Host 'then restart Windows and run this again.'
    exit 1
}
if (Get-Process -Name WindowsSandbox, WindowsSandboxClient, WindowsSandboxServer, WindowsSandboxRemoteSession -ErrorAction SilentlyContinue) {
    throw 'A Windows Sandbox is already open, and Windows runs one at a time. Close it and run this again.'
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
    "    <MappedFolder><HostFolder>$escaped</HostFolder><SandboxFolder>$desktop\$Name</SandboxFolder>" +
    "<ReadOnly>$($ReadOnly.ToString().ToLower())</ReadOnly></MappedFolder>"
}
$configuration = @(
    '<Configuration>',
    "  <MemoryInMB>$MemoryMB</MemoryInMB>",
    '  <MappedFolders>',
    (Get-MappedFolder $root 'apt-scraper' $true),
    (Get-MappedFolder $downloads 'toolchain' $true),
    (Get-MappedFolder $cache 'cache' $false),
    (Get-MappedFolder $out 'out' $false),
    '  </MappedFolders>',
    '  <LogonCommand>',
    "    <Command>powershell -NoProfile -ExecutionPolicy Bypass -File $desktop\apt-scraper\tests\sandbox-inside.ps1</Command>",
    '  </LogonCommand>',
    '</Configuration>'
) -join "`r`n"
$wsb = Join-Path $run 'checks.wsb'
Set-Content -Encoding ascii $wsb $configuration

Write-Host "==> Starting Windows Sandbox; its log is $out\log.txt" -ForegroundColor Cyan
Start-Process $sandbox -ArgumentList "`"$wsb`""

$log = Join-Path $out 'log.txt'
$exitFile = Join-Path $out 'exit-code'
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$script:shown = 0
function Show-NewLines {
    if (-not (Test-Path $log)) { return }
    $lines = @(Get-Content $log -ErrorAction SilentlyContinue)
    if ($lines.Count -gt $script:shown) {
        $lines[$script:shown..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
        $script:shown = $lines.Count
    }
}
while (-not (Test-Path $exitFile)) {
    if ((Get-Date) -gt $deadline) {
        Show-NewLines
        throw "The sandbox did not finish within $TimeoutMinutes minutes. Its log is $log."
    }
    Start-Sleep -Seconds 5
    Show-NewLines
}
Start-Sleep -Seconds 2
Show-NewLines
$code = [int](Get-Content $exitFile | Select-Object -First 1)
if ($code -eq 0) { Write-Host "==> The checks passed in the sandbox. Log: $log" -ForegroundColor Green }
else { Write-Host "==> The checks failed in the sandbox with exit code $code. Log: $log" -ForegroundColor Red }
exit $code
