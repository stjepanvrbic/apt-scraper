# What tests\sandbox.ps1 runs inside the Windows Sandbox through `wsb exec`, as System: unpacks Python, Git for
# Windows and Node from the mapped zip builds, copies the mapped checkout to a writable folder, runs tests\run.ps1
# with its output in out\log.txt, writes the exit code to out\exit-code and shuts the sandbox down. Not for
# running on a real machine.
# The toolchain comes as zip builds, not installers: the Windows Installer service inside a sandbox hung on
# Python's core MSI for ten minutes at a time, and unpacking needs no service at all.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Where the sandbox mounts the mapped folders, whichever account runs this.
$desktop = 'C:\Users\WDAGUtilityAccount\Desktop'
$env:APT_SCRAPER_SANDBOX = '1'
$source = Join-Path $desktop 'apt-scraper'
$toolchain = Join-Path $desktop 'toolchain'
$cache = Join-Path $desktop 'cache'
$out = Join-Path $desktop 'out'
$log = Join-Path $out 'log.txt'
$tools = 'C:\tools'
$work = 'C:\work\apt-scraper'
$code = 1
$options = $null

function Write-Log([string]$Message) {
    "$((Get-Date).ToString('HH:mm:ss')) $Message" | Add-Content -Encoding utf8 $log
    Write-Host $Message
}

try {
    $options = Get-Content (Join-Path $out 'options.json') -Raw | ConvertFrom-Json
    New-Item -ItemType Directory -Force $tools | Out-Null

    Write-Log 'Unpacking Python 3.14'
    # The NuGet build is a zip with python.exe, the standard library, venv, ensurepip and pip under tools\.
    Copy-Item (Join-Path $toolchain 'python.3.14.10.nupkg') (Join-Path $tools 'python.zip')
    Expand-Archive -Path (Join-Path $tools 'python.zip') -DestinationPath (Join-Path $tools 'python') -Force
    Remove-Item (Join-Path $tools 'python.zip')
    $python = Join-Path $tools 'python\tools'

    Write-Log 'Unpacking Git for Windows'
    # PortableGit is a self-extracting 7-Zip archive: -o names the folder, -y answers its one question.
    $git = Join-Path $tools 'git'
    Start-Process -Wait -FilePath (Join-Path $toolchain 'PortableGit-2.56.0.4-64-bit.7z.exe') -ArgumentList "-o`"$git`"", '-y'

    Write-Log 'Unpacking Node 24'
    Expand-Archive -Path (Join-Path $toolchain 'node-v24.22.2-win-x64.zip') -DestinationPath $tools -Force
    $node = Join-Path $tools 'node-v24.22.2-win-x64'

    $env:PATH = "$python;$python\Scripts;$git\cmd;$node;" + $env:PATH
    Write-Log ("Toolchain: " + (& "$python\python.exe" --version) + ', ' + (git --version) + ', node ' + (node --version))

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
    cmd /c "powershell -NoProfile -ExecutionPolicy Bypass -File `"$work\tests\run.ps1`" -Work C:\work\tests >> `"$log`" 2>&1"
    $code = $LASTEXITCODE
    Write-Log "tests\run.ps1 exited with $code"
} catch {
    Write-Log "Failed: $_"
} finally {
    Set-Content -Encoding ascii (Join-Path $out 'exit-code') $code
    if (-not ($options -and $options.keepOpen)) { shutdown /s /t 5 }
}
