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
    # A fresh Windows has no Visual C++ runtime, which onnxruntime's extension needs; a user's PC gets it with
    # Visual Studio or the redistributable. The msvc-runtime wheel puts the DLLs beside the base python.exe, where a
    # virtual environment's launcher, which runs that base interpreter, finds them.
    & "$python\python.exe" -m pip install --disable-pip-version-check --quiet msvc-runtime==14.51.36231
    if ($LASTEXITCODE -ne 0) { throw "Installing the Visual C++ runtime failed with exit code $LASTEXITCODE" }

    Write-Log 'Unpacking Git for Windows'
    # PortableGit is a self-extracting 7-Zip archive: -o names the folder, -y answers its one question.
    $git = Join-Path $tools 'git'
    $extractor = Start-Process -Wait -PassThru -FilePath (Join-Path $toolchain 'PortableGit-2.56.0.4-64-bit.7z.exe') -ArgumentList "-o`"$git`"", '-y'
    if ($extractor.ExitCode -ne 0) { throw "Unpacking Git for Windows failed with exit code $($extractor.ExitCode)" }
    if (-not (Test-Path (Join-Path $git 'usr\bin\bash.exe'))) { throw "Unpacking Git for Windows left no usr\bin\bash.exe in $git" }

    # The sandbox's Windows lacks the VBScript engine, which Windows 11 ships as an optional capability and the
    # dashboard's login entry and Start menu shortcut run through wscript.exe. The tests of those entries say so when
    # it is still missing after this.
    Write-Log 'Adding the VBScript capability'
    try {
        $capability = Add-WindowsCapability -Online -Name 'VBSCRIPT~~~~'
        Write-Log "VBScript: $((Get-WindowsCapability -Online -Name 'VBSCRIPT~~~~').State)$(if ($capability.RestartNeeded) { ', restart needed' })"
    } catch {
        Write-Log "VBScript could not be added: $_"
    }

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
    # Written whole, then renamed: the host takes the file's existence as the result, and must never read it empty.
    $exitFile = Join-Path $out 'exit-code'
    [IO.File]::WriteAllText("$exitFile.partial", [string]$code, [Text.Encoding]::ASCII)
    [IO.File]::Move("$exitFile.partial", $exitFile)
    if (-not ($options -and $options.keepOpen)) { shutdown /s /t 5 }
}
