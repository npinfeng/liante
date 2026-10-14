param(
    [Parameter(Mandatory=$true)][string]$Profile,
    [Parameter(Mandatory=$true)][PSCredential]$ServiceCredential
)
$ErrorActionPreference='Stop'
$profilePath=[IO.Path]::GetFullPath($Profile)
$deployment=Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
if($deployment.service_account -ne $ServiceCredential.UserName){throw '服务账号与 profile 不一致'}
$runner=Join-Path $deployment.project_directory 'deploy\run_profile.ps1'
$arguments="-NoProfile -WindowStyle Hidden -File `"$runner`" -Profile `"$profilePath`""
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $deployment.project_directory
$taskSettings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $deployment.service_name -Action $action -Settings $taskSettings -User $ServiceCredential.UserName -Password $ServiceCredential.GetNetworkCredential().Password -Force | Out-Null
Disable-ScheduledTask -TaskName $deployment.service_name | Out-Null
Write-Output '服务任务已登记，使用 lifecycle.ps1 start 启动；未启用开机自启'
