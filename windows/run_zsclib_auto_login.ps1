param(
    [string]$TargetSsid = "ZSClib",
    [int]$CdpPort = 9222,
    [string]$ChromeProfile = "C:\BrowserProfiles\ZSClibAutoLogin",
    [string]$TriggerUrl = "http://www.msftconnecttest.com/redirect",
    [int]$SsidEventFallbackMaxAgeSeconds = 1800,
    [int]$WaitForTargetSsidSeconds = 60,
    [int]$SsidPollIntervalSeconds = 2,
    [switch]$ReloadHarnessDaemon
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LoginScript = Join-Path $RepoRoot "scripts\zsclib_auto_login.py"
$LogDir = Join-Path $RepoRoot "logs"
$LogFile = Join-Path $LogDir "zsclib_auto_login.log"
$CdpUrl = "http://127.0.0.1:$CdpPort"
$RunnerVersion = "2026-05-17-startup-autoconnect-v4"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-CurrentSsid {
    $output = & netsh wlan show interfaces 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log "netsh failed: $output"
        return Get-RecentConnectedSsidFromEventLog
    }

    foreach ($line in $output) {
        if ($line -match '^\s*SSID\s*:\s*(.+?)\s*$' -and $line -notmatch '^\s*BSSID\s*:') {
            return $Matches[1].Trim()
        }
    }
    Write-Log "netsh did not report a connected SSID; trying WLAN event log fallback."
    return Get-RecentConnectedSsidFromEventLog
}

function Get-RecentConnectedSsidFromEventLog {
    try {
        $cutoff = (Get-Date).AddSeconds(-1 * $SsidEventFallbackMaxAgeSeconds)
        $events = Get-WinEvent -LogName "Microsoft-Windows-WLAN-AutoConfig/Operational" -MaxEvents 40 -ErrorAction Stop |
            Where-Object { $_.Id -eq 8001 -and $_.TimeCreated -ge $cutoff }

        foreach ($event in $events) {
            [xml]$xml = $event.ToXml()
            $ssidNode = $xml.Event.EventData.Data | Where-Object { $_.Name -eq "SSID" } | Select-Object -First 1
            $ssid = if ($ssidNode) { [string]$ssidNode.'#text' } else { "" }
            if ($ssid) {
                Write-Log "SSID from recent WLAN event fallback: $ssid at $($event.TimeCreated)"
                return $ssid.Trim()
            }
        }

        Write-Log "WLAN event fallback found no connection event within ${SsidEventFallbackMaxAgeSeconds}s."
    } catch {
        Write-Log "WLAN event fallback failed: $($_.Exception.Message)"
    }
    return $null
}

function Wait-ForTargetSsid {
    $deadline = (Get-Date).AddSeconds($WaitForTargetSsidSeconds)
    $ssid = $null

    while ($true) {
        $ssid = Get-CurrentSsid
        Write-Log "current SSID: $ssid"
        if ($ssid -eq $TargetSsid) {
            return $ssid
        }

        $remainingSeconds = [int][Math]::Ceiling(($deadline - (Get-Date)).TotalSeconds)
        if ($remainingSeconds -le 0 -or $WaitForTargetSsidSeconds -le 0) {
            return $ssid
        }

        $sleepSeconds = [Math]::Min($SsidPollIntervalSeconds, $remainingSeconds)
        Write-Log "waiting for SSID $TargetSsid; current SSID: $ssid; retrying in ${sleepSeconds}s"
        Start-Sleep -Seconds $sleepSeconds
    }
}

function Test-CdpEndpoint {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$Url/json/version" -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

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

    return $null
}

function Start-DedicatedChrome {
    param([string]$InitialUrl = "about:blank")

    if (Test-CdpEndpoint -Url $CdpUrl) {
        Write-Log "CDP endpoint already available at $CdpUrl"
        return
    }

    $chrome = Find-Chrome
    if (-not $chrome) {
        throw "chrome.exe not found. Install Google Chrome or add chrome.exe to PATH."
    }

    New-Item -ItemType Directory -Force -Path $ChromeProfile | Out-Null
    $args = @(
        "--remote-debugging-port=$CdpPort",
        "--user-data-dir=$ChromeProfile",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        $InitialUrl
    )

    Write-Log "starting Chrome: $chrome $($args -join ' ')"
    Start-Process -FilePath $chrome -ArgumentList $args -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (Test-CdpEndpoint -Url $CdpUrl) {
            Write-Log "CDP endpoint is ready at $CdpUrl"
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Chrome did not expose $CdpUrl/json/version within 20 seconds."
}

function Stop-DedicatedChrome {
    $portNeedle = "--remote-debugging-port=$CdpPort"
    $profileNeedle = "--user-data-dir=$ChromeProfile"

    try {
        $processes = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine.Contains($portNeedle) -and
                $_.CommandLine.Contains($profileNeedle)
            }

        foreach ($process in $processes) {
            Write-Log "closing dedicated Chrome process id=$($process.ProcessId)"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Log "failed to close dedicated Chrome: $($_.Exception.Message)"
    }
}

function Get-HarnessLaunch {
    $cmd = Get-Command browser-harness -ErrorAction SilentlyContinue
    if ($cmd) {
        return @{
            FileName = $cmd.Source
            Arguments = ""
            PythonPath = $null
        }
    }

    $localExe = Join-Path $RepoRoot ".venv\Scripts\browser-harness.exe"
    if (Test-Path -LiteralPath $localExe) {
        return @{
            FileName = $localExe
            Arguments = ""
            PythonPath = $null
        }
    }

    throw "browser-harness is not installed. From $RepoRoot run: uv tool install -e ."
}

function Invoke-BrowserHarnessReload {
    param([hashtable]$Launch)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Launch.FileName
    $psi.Arguments = "--reload"
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables["BU_CDP_URL"] = $CdpUrl
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $psi.EnvironmentVariables["SSLKEYLOGFILE"] = ""

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    if ($stdout) {
        Add-Content -LiteralPath $LogFile -Value $stdout.TrimEnd() -Encoding UTF8
    }
    if ($stderr) {
        Add-Content -LiteralPath $LogFile -Value $stderr.TrimEnd() -Encoding UTF8
        Write-Log "browser-harness reload stderr: $($stderr.TrimEnd())"
    }
    if ($process.ExitCode -ne 0) {
        Write-Log "browser-harness reload exited with code $($process.ExitCode); continuing with fresh run attempt."
    }
}

function Invoke-BrowserHarnessScript {
    $launch = Get-HarnessLaunch
    if ($ReloadHarnessDaemon) {
        Invoke-BrowserHarnessReload -Launch $launch
    } else {
        Write-Log "skipping browser-harness daemon reload."
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $launch.FileName
    $psi.Arguments = $launch.Arguments
    $psi.WorkingDirectory = $RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables["BU_CDP_URL"] = $CdpUrl
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $psi.EnvironmentVariables["SSLKEYLOGFILE"] = ""

    if ($launch.PythonPath) {
        $existing = $psi.EnvironmentVariables["PYTHONPATH"]
        if ($existing) {
            $psi.EnvironmentVariables["PYTHONPATH"] = "$($launch.PythonPath);$existing"
        } else {
            $psi.EnvironmentVariables["PYTHONPATH"] = $launch.PythonPath
        }
    }

    $scriptText = Get-Content -LiteralPath $LoginScript -Raw -Encoding UTF8
    $process = [System.Diagnostics.Process]::Start($psi)
    $process.StandardInput.Write($scriptText)
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    if ($stdout) {
        Add-Content -LiteralPath $LogFile -Value $stdout.TrimEnd() -Encoding UTF8
        Write-Host $stdout.TrimEnd()
    }
    if ($stderr) {
        Add-Content -LiteralPath $LogFile -Value $stderr.TrimEnd() -Encoding UTF8
        Write-Log "browser-harness stderr: $($stderr.TrimEnd())"
    }

    return $process.ExitCode
}

try {
    Write-Log "runner version: $RunnerVersion; repo: $RepoRoot; trigger: $TriggerUrl; cdp: $CdpUrl"
    $ssid = Wait-ForTargetSsid
    if ($ssid -ne $TargetSsid) {
        Write-Log "SSID is not $TargetSsid; skipping portal login."
        exit 0
    }

    if (-not (Test-Path -LiteralPath $LoginScript)) {
        throw "login script not found: $LoginScript"
    }

    Stop-DedicatedChrome
    Start-DedicatedChrome -InitialUrl $TriggerUrl
    Write-Log "running browser-harness with BU_CDP_URL=$CdpUrl"
    $exitCode = Invoke-BrowserHarnessScript
    Write-Log "browser-harness exited with code $exitCode"
    Stop-DedicatedChrome
    exit $exitCode
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    Write-Log "STACK: $($_.ScriptStackTrace)"
    Stop-DedicatedChrome
    exit 3
}
