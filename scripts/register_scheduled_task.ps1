[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WorkspacePath,

    [string]$UvPath,

    [string]$ConfigPath,

    [string]$TaskName = 'Tkn-Itadaki-WeeklyPipeline'
)

$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path -LiteralPath $WorkspacePath).Path
if ($UvPath) {
    $uv = (Resolve-Path -LiteralPath $UvPath).Path
}
else {
    $uv = (Get-Command uv -ErrorAction Stop).Source
}
$arguments = 'run --frozen tkn-itadaki-pipeline ingest'
if ($ConfigPath) {
    $config = (Resolve-Path -LiteralPath $ConfigPath).Path
    $arguments += " --config `"$config`""
}

$action = New-ScheduledTaskAction `
    -Execute $uv `
    -Argument $arguments `
    -WorkingDirectory $workspace
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '03:00'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Archive Itadaki Rec logs and build monthly processed CSV datasets.' `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName
