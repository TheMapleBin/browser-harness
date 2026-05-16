param(
    [string]$TaskName = "ZSClib Portal Auto Login",
    [string]$TargetSsid = "ZSClib",
    [int]$DelaySeconds = 30,
    [int]$WlanEventDelaySeconds = 1,
    [switch]$NoWlanEvent,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunScript = Join-Path $PSScriptRoot "run_zsclib_auto_login.ps1"

if (-not (Test-Path -LiteralPath $RunScript)) {
    throw "run script not found: $RunScript"
}

function Escape-Xml {
    param([string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

function Quote-ProcessArgument {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-SchtasksCreate {
    param(
        [string]$Name,
        [string]$XmlFile
    )

    $arguments = @(
        "/Create",
        "/TN", $Name,
        "/XML", $XmlFile,
        "/F"
    ) | ForEach-Object { Quote-ProcessArgument -Value $_ }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "schtasks.exe"
    $psi.Arguments = ($arguments -join " ")
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return @{
        ExitCode = $process.ExitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

try {
    & wevtutil sl "Microsoft-Windows-WLAN-AutoConfig/Operational" /e:true 2>$null
} catch {
    Write-Warning "Could not enable WLAN AutoConfig Operational log. If the WLAN event trigger does not fire, enable it manually in Event Viewer."
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = Escape-Xml $identity.User.Value
$escapedRepoRoot = Escape-Xml $RepoRoot
$actionArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $RunScript
$escapedActionArgs = Escape-Xml $actionArgs
$escapedTaskName = Escape-Xml $TaskName
$escapedTargetSsid = Escape-Xml $TargetSsid

$wlanTriggerXml = ""
if (-not $NoWlanEvent) {
    $wlanSubscription = @"
<QueryList>
  <Query Id="0" Path="Microsoft-Windows-WLAN-AutoConfig/Operational">
    <Select Path="Microsoft-Windows-WLAN-AutoConfig/Operational">*[System[(EventID=8001)] and EventData[Data[@Name='SSID']='$TargetSsid']]</Select>
  </Query>
</QueryList>
"@

    $wlanTriggerXml = @"
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription><![CDATA[$wlanSubscription]]></Subscription>
      <Delay>PT${WlanEventDelaySeconds}S</Delay>
    </EventTrigger>
"@
}

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Run ZSClib captive portal auto login after user logon or after connecting to the target WLAN SSID.</Description>
    <URI>\$escapedTaskName</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT${DelaySeconds}S</Delay>
    </LogonTrigger>
$wlanTriggerXml
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$escapedActionArgs</Arguments>
      <WorkingDirectory>$escapedRepoRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = Join-Path $env:TEMP "zsclib_auto_login_task.xml"
Set-Content -LiteralPath $xmlPath -Value $taskXml -Encoding Unicode

try {
    Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force -ErrorAction Stop | Out-Null
} catch {
    $registerError = $_.Exception.Message
    $schtasksResult = Invoke-SchtasksCreate -Name $TaskName -XmlFile $xmlPath
    $schtasksOutput = (($schtasksResult.StdOut, $schtasksResult.StdErr) -join "").Trim()
    if ($schtasksResult.ExitCode -ne 0) {
        throw "Register-ScheduledTask failed: $registerError; schtasks.exe failed with code $($schtasksResult.ExitCode): $schtasksOutput"
    }
}

$registeredTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $registeredTask) {
    throw "Task registration did not produce a readable scheduled task named '$TaskName'. XML snapshot: $xmlPath"
}

Write-Output "Registered task: $TaskName"
Write-Output "Action: powershell.exe $actionArgs"
Write-Output "Trigger: At logon, delay ${DelaySeconds}s"
if (-not $NoWlanEvent) {
    Write-Output "Trigger: WLAN AutoConfig EventID=8001, SSID=$escapedTargetSsid, delay ${WlanEventDelaySeconds}s"
}
Write-Output "Verified task state: $($registeredTask.State)"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Started task: $TaskName"
}
