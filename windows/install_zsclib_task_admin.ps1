param(
    [int]$WlanEventDelaySeconds = 1
)

$ErrorActionPreference = "Stop"

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
$createScript = Join-Path $PSScriptRoot "create_zsclib_task.ps1"

if (-not $isAdmin) {
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-NoExit",
        "-File", "`"$PSCommandPath`"",
        "-WlanEventDelaySeconds", $WlanEventDelaySeconds
    )
    Start-Process powershell.exe -Verb RunAs -ArgumentList $args
    exit 0
}

& $createScript -WlanEventDelaySeconds $WlanEventDelaySeconds

Write-Output ""
Write-Output "Task XML snapshot:"
Write-Output (Join-Path $env:TEMP "zsclib_auto_login_task.xml")
Write-Output ""
Write-Output "Press Enter to close this administrator window."
[void][Console]::ReadLine()
