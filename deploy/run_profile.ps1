param([Parameter(Mandatory=$true)][string]$Profile)
$ErrorActionPreference='Stop'
$deployment=Get-Content -LiteralPath $Profile -Raw | ConvertFrom-Json
Set-Location -LiteralPath $deployment.project_directory
$settings=Get-Content -LiteralPath (Join-Path $deployment.project_directory $deployment.settings_file) -Raw | ConvertFrom-Json
$settings.psobject.Properties | ForEach-Object{[Environment]::SetEnvironmentVariable($_.Name,[string]$_.Value,'Process')}
$env:APP_ENV=$deployment.environment
$env:HOST=$deployment.listen_address
$env:PORT=[string]$deployment.port
& $deployment.python_executable tools/validate_runtime.py
if($LASTEXITCODE -ne 0){throw '配置、目录或数据库检查失败'}
& $deployment.python_executable serve.py
exit $LASTEXITCODE
