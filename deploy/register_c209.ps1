$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$settingsDir = Join-Path $projectRoot '.secrets'
$settingsPath = Join-Path $settingsDir 'c209.json'
New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
function New-Secret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}
if (-not (Test-Path -LiteralPath $settingsPath)) {
    $settings = [ordered]@{
        LIANTE_ADMIN_TOKEN = New-Secret
        LIANTE_OPERATOR_TOKEN = New-Secret
        LIANTE_DEVICE_TOKEN = New-Secret
        LIANTE_DEVICE_TOKENS = (@{ 'MES-STAGE2' = (New-Secret) } | ConvertTo-Json -Compress)
        LIANTE_ALLOWED_ORIGINS = 'https://10.254.7.25:8443'
        PREDICTION_ALLOWED_DIRS = 'F:\npfcode\liante\data;F:\npfcode\liante_old\predict_data'
        LIANTE_MODEL_DIR = 'F:\npfcode\liante\saved_models'
        LIANTE_TRAIN_DATA_DIR = 'F:\npfcode\liante_old\train_data\data_v5\split_by_channel'
        STAGE2_INPUT_DIRS = 'F:\npfcode\liante_old\train_data\data_v5\split_by_channel'
        STAGE2_ALLOWED_DIRS = 'F:\npfcode\liante\data\stage2_input'
        DATA_DIR = 'F:\npfcode\liante\data'
        OUTPUT_DIR = 'F:\npfcode\liante\output'
        LOG_DIR = 'F:\npfcode\liante\logs'
        WEB_TRAIN_DEVICE = 'cpu'
    }
    $settings | ConvertTo-Json | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    & icacls.exe $settingsPath /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$serverAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $projectRoot 'deploy\start_c209.ps1') + '"')
$caddyExe = Join-Path $projectRoot '.deploy\caddy\caddy.exe'
if (-not (Test-Path -LiteralPath $caddyExe)) { throw 'Install Caddy before registering tasks' }
$caddyAction = New-ScheduledTaskAction -Execute $caddyExe -Argument (
    'run --config "' + (Join-Path $projectRoot 'deploy\Caddyfile') + '" --adapter caddyfile')
Register-ScheduledTask -TaskName 'LianteWebServer' -Action $serverAction -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Register-ScheduledTask -TaskName 'LianteHttpsProxy' -Action $caddyAction -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'Scheduled tasks registered: LianteWebServer, LianteHttpsProxy'
