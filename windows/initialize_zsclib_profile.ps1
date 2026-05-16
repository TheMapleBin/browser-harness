param(
    [int]$CdpPort = 9222,
    [string]$ChromeProfile = "C:\BrowserProfiles\ZSClibAutoLogin",
    [string]$TriggerUrl = "http://www.msftconnecttest.com/redirect",
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LocalAppData "Google\Chrome\Application\chrome.exe")
    )

    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }

    $cmd = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "chrome.exe was not found. Install Google Chrome first."
}

$chrome = Find-Chrome
New-Item -ItemType Directory -Force -Path $ChromeProfile | Out-Null

$chromeArgs = @(
    "--remote-debugging-port=$CdpPort",
    "--user-data-dir=$ChromeProfile",
    "--no-first-run",
    "--no-default-browser-check",
    $TriggerUrl
)

Write-Output "Chrome profile: $ChromeProfile"
Write-Output "Trigger URL: $TriggerUrl"
Write-Output ""
Write-Output "Use the Chrome window that opens now:"
Write-Output "  1. Connect this PC to ZSClib."
Write-Output "  2. If the portal appears, enter account and password."
Write-Output "  3. Check remember password on the portal page."
Write-Output "  4. Click the portal login/confirm button once."
Write-Output "  5. Close Chrome after the network is online."
Write-Output ""
Write-Output "This project does not store the username or password. They stay in the Chrome profile above."

if ($NoWait) {
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs | Out-Null
} else {
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs -Wait | Out-Null
}
