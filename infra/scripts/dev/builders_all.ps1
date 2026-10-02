# Start everything for local testing: cadastral (8011), planning (8012), web (3000).
# Each one is started only if its port is not already answering. Prints the links when ready.
# Data paths default to this machine's layout (CLAUDE.md); override with the env vars below.
# Local dev only: DEV_BYPASS_AUTH=1 disables JWT checks. Never use this for a deployment.

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$logs = if ($env:BUILDERS_LOG_DIR) { $env:BUILDERS_LOG_DIR } else { "$env:TEMP\builders_logs" }
New-Item -ItemType Directory -Force $logs | Out-Null
$cadData = if ($env:CADASTRAL_DATA_DIR) { $env:CADASTRAL_DATA_DIR } else { "C:\Users\tanny\Downloads\cadastral_lake_v2\cadastral_lake_v2" }
$surveyDb = if ($env:SURVEY_INDEX_DB) { $env:SURVEY_INDEX_DB } else { "C:\Users\tanny\Downloads\survey_index\survey_index.db" }
$planData = if ($env:PLANNING_DATA_DIR) { $env:PLANNING_DATA_DIR } else { "C:\Users\tanny\Downloads\planning\planning\zones" }
$planFlags = "feature.planning.layers feature.planning.plan.BDA-RMP2031 feature.planning.plan.BMRDA-HSK-MP2031 feature.planning.plan.BMRDA-ANK-MP2031"

function Up($url) {
  try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

$common = @{
  DEV_BYPASS_AUTH = "1"; KEYCLOAK_URL = "https://auth.builder.qnit.site"; KEYCLOAK_REALM = "sat"
  CORS_ORIGINS = '["http://localhost:3000"]'
}
foreach ($k in $common.Keys) { Set-Item "env:$k" $common[$k] }

if (-not (Up "http://localhost:8011/health")) {
  Write-Host "Starting cadastral on :8011"
  $env:FLAGS = "feature.cadastral.land-records"
  $env:CADASTRAL_DATA_DIR = $cadData
  $env:SURVEY_INDEX_DB = $surveyDb
  Start-Process -FilePath "$repo\services\cadastral\.venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app", "--port", "8011" `
    -WorkingDirectory "$repo\services\cadastral" -RedirectStandardOutput "$logs\cadastral.log" -RedirectStandardError "$logs\cadastral.err" -WindowStyle Hidden
} else { Write-Host "cadastral already running on :8011" }

if (-not (Up "http://localhost:8012/health")) {
  Write-Host "Starting planning on :8012"
  $env:FLAGS = $planFlags
  $env:PLANNING_DATA_DIR = $planData
  $env:PLANNING_REGISTER_DIR = "$repo\infra\planning"
  $env:CADASTRAL_URL = "http://localhost:8011"
  Start-Process -FilePath "$repo\services\planning\.venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app", "--port", "8012" `
    -WorkingDirectory "$repo\services\planning" -RedirectStandardOutput "$logs\planning.log" -RedirectStandardError "$logs\planning.err" -WindowStyle Hidden
} else { Write-Host "planning already running on :8012" }

if (-not (Up "http://localhost:3000")) {
  Write-Host "Starting web on :3000"
  $env:NEXT_PUBLIC_ENABLE_PLANNING_LAYERS = "1"
  $env:NEXT_PUBLIC_ENABLE_CADASTRAL_EXPLORER = "1"
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev > `"$logs\web.log`" 2> `"$logs\web.err`"" `
    -WorkingDirectory "$repo\apps\web" -WindowStyle Hidden
} else { Write-Host "web already running on :3000" }

$t0 = Get-Date
foreach ($u in "http://localhost:8011/health", "http://localhost:8012/health", "http://localhost:3000") {
  while (-not (Up $u)) {
    if (((Get-Date) - $t0).TotalSeconds -gt 180) { Write-Host "Timed out waiting for $u; logs in $logs"; exit 1 }
    Start-Sleep 2
  }
}
Write-Host "Warming the planning layers (first load about 2-3 min)..."
foreach ($q in "plan_id=BDA-RMP2031&bbox=77.58,12.96,77.60,12.98", "plan_id=BMRDA-HSK-MP2031&bbox=77.79,13.07,77.81,13.09", "plan_id=BMRDA-ANK-MP2031&bbox=77.69,12.78,77.71,12.80") {
  try { Invoke-WebRequest "http://localhost:8012/zones?$q" -UseBasicParsing -TimeoutSec 600 | Out-Null } catch { Write-Host "warm-up failed for $q (see $logs\planning.err)" }
}

Write-Host ""
Write-Host "Ready:"
Write-Host "  Map (dashboard):  http://localhost:3000/dashboard"
Write-Host "  Planning API:     http://localhost:8012/docs"
Write-Host "  Cadastral API:    http://localhost:8011/docs"
Write-Host "  Logs:             $logs"
