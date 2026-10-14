param(
    [Parameter(Mandatory=$true)][ValidateSet('start','stop','drain','status','restart')][string]$Action,
    [Parameter(Mandatory=$true)][string]$Profile,
    [int]$TimeoutSeconds=600
)
$ErrorActionPreference='Stop'
$deployment=Get-Content -LiteralPath $Profile -Raw | ConvertFrom-Json
$project=[IO.Path]::GetFullPath($deployment.project_directory)
$settings=Get-Content -LiteralPath (Join-Path $project $deployment.settings_file) -Raw | ConvertFrom-Json
$settings.psobject.Properties | ForEach-Object{[Environment]::SetEnvironmentVariable($_.Name,[string]$_.Value,'Process')}
$env:APP_ENV=$deployment.environment
$env:HOST=$deployment.listen_address
$env:PORT=[string]$deployment.port
$base="http://127.0.0.1:$($deployment.port)"
$token=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("admin:$($settings.LIANTE_ADMIN_TOKEN)"))
$headers=@{Authorization="Basic $token"}
function Wait-Drained {
    Invoke-RestMethod -Uri "$base/api/v1/admin/drain" -Method Post -Headers $headers | Out-Null
    $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $health=(Invoke-RestMethod -Uri "$base/api/v1/health").data
        $busy=[int]$health.aoi_streams.active_streams+[int]$health.training_queue.outstanding
        $health.inference_queue.psobject.Properties | ForEach-Object{$busy += [int]$_.Value.outstanding}
        if($busy -eq 0){return}
        Start-Sleep -Seconds 1
    } while((Get-Date) -lt $deadline)
    throw '排空超时；服务继续运行，未强制停止'
}
function Stop-Project {
    try {$alive=(Invoke-RestMethod -Uri "$base/api/v1/live").data.alive} catch {$alive=$false}
    if($alive){Wait-Drained}
    Stop-ScheduledTask -TaskName $deployment.service_name
    $lock=Join-Path $project '.runtime\server.json'
    if(Test-Path -LiteralPath $lock){
        $server=Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
        $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($server.pid)"
        if($process -and $process.CommandLine -match [regex]::Escape((Join-Path $project 'serve.py')) -and $server.project -eq $project){Stop-Process -Id $server.pid -Force}
    }
}
function Start-Project {
    if(-not(Test-Path -LiteralPath $deployment.python_executable)){throw 'Python 路径不存在'}
    Set-Location -LiteralPath $project
    & $deployment.python_executable tools/validate_runtime.py
    if($LASTEXITCODE -ne 0){throw '启动前配置检查失败'}
    Enable-ScheduledTask -TaskName $deployment.service_name | Out-Null
    try {Start-ScheduledTask -TaskName $deployment.service_name} finally {Disable-ScheduledTask -TaskName $deployment.service_name | Out-Null}
    $deadline=(Get-Date).AddSeconds(60)
    do {
        try {if((Invoke-RestMethod -Uri "$base/api/v1/live").data.alive){Write-Output '服务已启动';return}} catch {}
        Start-Sleep -Seconds 1
    } while((Get-Date) -lt $deadline)
    throw '服务启动健康检查未通过'
}
switch($Action){
    'status' {Invoke-RestMethod -Uri "$base/api/v1/system/info" -Headers $headers | ConvertTo-Json -Depth 12}
    'drain' {Wait-Drained;Write-Output '在途任务已排空'}
    'stop' {Stop-Project;Write-Output '项目服务已停止'}
    'start' {Start-Project}
    'restart' {Stop-Project;Start-Project}
}
