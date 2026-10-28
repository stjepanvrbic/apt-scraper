# What tests\sandbox.ps1 runs inside the Windows Sandbox at logon: installs Python, Git for Windows and Node from
# the mapped installers, copies the mapped checkout to a writable folder, runs tests\run.ps1 with its output in
# out\log.txt, writes the exit code to out\exit-code and shuts the sandbox down. Not for running on a real machine.
$ErrorActionPreference = 'Stop'
$desktop = Join-Path $env:USERPROFILE 'Desktop'
$source = Join-Path $desktop 'apt-scraper'
$toolchain = Join-Path $desktop 'toolchain'
$cache = Join-Path $desktop 'cache'
$out = Join-Path $desktop 'out'
$log = Join-Path $out 'log.txt'
$work = 'C:\work\apt-scraper'
$code = 1
$options = $null

function Write-Log([string]$Message) {
    "$((Get-Date).ToString('HH:mm:ss')) $Message" | Add-Content -Encoding utf8 $log
    Write-Host $Message
}

try {
    $options = Get-Content (Join-Path $out 'options.json') -Raw | ConvertFrom-Json
    Write-Log 'Installing Python 3.14'
    Start-Process -Wait -FilePath (Join-Path $toolchain 'python-3.14.10-amd64.exe') `
        -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0', '/log', (Join-Path $out 'python-install.log')
    Write-Log 'Installing Git for Windows'
    Start-Process -Wait -FilePath (Join-Path $toolchain 'Git-2.56.0.4-64-bit.exe') `
        -ArgumentList '/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', ('/LOG=' + (Join-Path $out 'git-install.log'))
    Write-Log 'Installing Node 24'
    Start-Process -Wait -FilePath msiexec -ArgumentList '/i', (Join-Path $toolchain 'node-v24.22.2-x64.msi'), '/qn', '/norestart', '/log', (Join-Path $out 'node-install.log')
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')

    Write-Log "Copying the checkout to $work"
    New-Item -ItemType Directory -Force $work | Out-Null
    robocopy $source $work /E /NFL /NDL /NJH /NJS /NP /XD node_modules .gallery-test .ranking-work test-results playwright-report __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

    foreach ($name in 'pip', 'npm', 'playwright') { New-Item -ItemType Directory -Force (Join-Path $cache $name) | Out-Null }
    $env:PIP_CACHE_DIR = Join-Path $cache 'pip'
    $env:npm_config_cache = Join-Path $cache 'npm'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $cache 'playwright'

    Write-Log 'Running tests\run.ps1'
    # cmd's redirection takes both streams as the checks print them, which PowerShell 5.1 would rewrap.
    cmd /c "powershell -NoProfile -ExecutionPolicy Bypass -File `"$work\tests\run.ps1`" -Work C:\work\tests >>`"$log`" 2>&1"
    $code = $LASTEXITCODE
    Write-Log "tests\run.ps1 exited with $code"
} catch {
    Write-Log "Failed: $_"
} finally {
    Set-Content -Encoding ascii (Join-Path $out 'exit-code') $code
    if (-not ($options -and $options.keepOpen)) { shutdown /s /t 5 }
}
