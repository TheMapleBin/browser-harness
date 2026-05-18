param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Find-PythonLauncher {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            File = $py.Source
            Args = @("-3")
        }
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            File = $python.Source
            Args = @()
        }
    }

    throw "Python 3.11 or newer was not found. Install Python from https://www.python.org/downloads/windows/ and enable PATH."
}

function Invoke-Checked {
    param(
        [string]$File,
        [string[]]$Arguments,
        [string]$Description
    )

    Write-Output "==> $Description"
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

Set-Location $RepoRoot

$launcher = Find-PythonLauncher
$versionArgs = @()
$versionArgs += $launcher.Args
$versionArgs += @("-c", "import sys; print(sys.version); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
Invoke-Checked -File $launcher.File -Arguments $versionArgs -Description "Checking Python version"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $venvArgs = @()
    $venvArgs += $launcher.Args
    $venvArgs += @("-m", "venv", $VenvDir)
    Invoke-Checked -File $launcher.File -Arguments $venvArgs -Description "Creating local virtual environment"
} else {
    Write-Output "==> Reusing existing virtual environment: $VenvDir"
}

Invoke-Checked -File $VenvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip") -Description "Upgrading pip"
Invoke-Checked -File $VenvPython -Arguments @("-m", "pip", "install", "-e", ".") -Description "Installing browser-harness in editable mode"

if (-not $SkipTests) {
    Invoke-Checked -File $VenvPython -Arguments @("-m", "unittest", "tests.unit.test_zsclib_auto_login", "tests.unit.test_zsclib_windows_scripts") -Description "Running ZSClib portable smoke tests"
}

Write-Output ""
Write-Output "Portable setup completed."
Write-Output "Next:"
Write-Output "  1. Connect this PC to ZSClib."
Write-Output "  2. Run: powershell.exe -ExecutionPolicy Bypass -File `"$PSScriptRoot\initialize_zsclib_profile.ps1`""
Write-Output "  3. In the opened Chrome profile, log in once, check remember password, and close Chrome."
Write-Output "  4. Run: powershell.exe -ExecutionPolicy Bypass -File `"$PSScriptRoot\install_zsclib_task_admin.ps1`" -WlanEventDelaySeconds 1"
