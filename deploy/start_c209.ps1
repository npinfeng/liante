param(
    [string]$AdditionalAllowedOrigins = '',
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$ListenAddress = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$settingsPath = Join-Path $projectRoot '.secrets\c209.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    throw "Missing deployment settings: $settingsPath"
}
$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($item in $settings.PSObject.Properties) {
    [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process')
}
if ($AdditionalAllowedOrigins) {
    $env:LIANTE_ALLOWED_ORIGINS = ((@($env:LIANTE_ALLOWED_ORIGINS -split ',') +
        @($AdditionalAllowedOrigins -split ',')) | Where-Object { $_ } | Select-Object -Unique) -join ','
}
$env:HOST = ([Net.IPAddress]::Parse($ListenAddress)).ToString()
$env:PORT = [string]$Port
$env:DEBUG = 'false'
$env:APP_ENV = 'production'
Set-Location -LiteralPath $projectRoot
$profile=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'profiles\c209.json') -Raw | ConvertFrom-Json
& $profile.python_executable (Join-Path $projectRoot 'tools\validate_runtime.py')
if($LASTEXITCODE -ne 0){throw 'Runtime preflight failed'}
& $profile.python_executable (Join-Path $projectRoot 'serve.py')
exit $LASTEXITCODE
