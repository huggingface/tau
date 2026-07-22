$ErrorActionPreference = "Stop"

$UvInstallerUrl = "https://astral.sh/uv/install.ps1"

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @()
    if ($env:UV_INSTALL_DIR) {
        $candidates += Join-Path $env:UV_INSTALL_DIR "uv.exe"
    }
    if ($env:XDG_BIN_HOME) {
        $candidates += Join-Path $env:XDG_BIN_HOME "uv.exe"
    }
    $candidates += Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    $candidates += Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}

$uv = Find-Uv
if (-not $uv) {
    Write-Host "Tau uses uv to manage its isolated Python environment."
    Write-Host "uv was not found, so the official uv installer will be run now."

    $installer = Invoke-RestMethod $UvInstallerUrl
    Invoke-Expression $installer

    $uv = Find-Uv
    if (-not $uv) {
        throw "uv was installed but its executable could not be found. Open a new terminal and run: uv tool install tau-ai"
    }
}

Write-Host "Installing Tau with $uv ..."
& $uv tool install tau-ai
if ($LASTEXITCODE -ne 0) {
    throw "uv could not install Tau."
}

$toolBin = (& $uv tool dir --bin | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "uv could not locate its tool executable directory."
}
$tau = Join-Path $toolBin "tau.exe"
if (-not (Test-Path -LiteralPath $tau -PathType Leaf)) {
    throw "Tau was installed but $tau was not found."
}

& $tau --version
if ($LASTEXITCODE -ne 0) {
    throw "Tau was installed but could not be started."
}
Write-Host "Tau is installed. Run: tau"

$pathEntries = $env:PATH -split ";"
if ($toolBin -notin $pathEntries) {
    Write-Host "Restart PowerShell if 'tau' is not found; $toolBin must be on PATH."
}
