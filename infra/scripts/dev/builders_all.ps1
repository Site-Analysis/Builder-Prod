# Start everything for local testing: cadastral (8011), planning (8012), web (3000).
# Each one is started only if its port is not already answering. Prints the links when ready.
#   -Stop   stops the three services and deletes their logs (logs are temporary).
# Planning layers are not read from disk: the service downloads and extracts each plan sheet
# on first view (layer index, infra/planning/layer_index.json) and keeps it in RAM only.
# Data paths default to this machine's layout (CLAUDE.md); override with the env vars below.
# Local dev only: DEV_BYPASS_AUTH=1 disables JWT checks. Never use this for a deployment.
param([switch]$Stop)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$logs = Join-Path $env:TEMP "qnit_planning\logs\services"

function Up($url) {
  try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

function Stop-Port($port) {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($p in ($c | Select-Object -ExpandProperty OwningProcess -Unique)) {
    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
  }
}

if ($Stop) {
  foreach ($port in 3000, 8012, 8011) { Stop-Port $port }
  Start-Sleep 2
  Remove-Item -Recurse -Force $logs -ErrorAction SilentlyContinue
  Write-Host "stopped; logs deleted"
  exit 0
}

New-Item -ItemType Directory -Force $logs | Out-Null
$cadData = if ($env:CADASTRAL_DATA_DIR) { $env:CADASTRAL_DATA_DIR } else { "C:\Users\tanny\Downloads\cadastral_lake_v2\cadastral_lake_v2" }
$surveyDb = if ($env:SURVEY_INDEX_DB) { $env:SURVEY_INDEX_DB } else { "C:\Users\tanny\Downloads\survey_index\survey_index.db" }
$plans = @(Get-Content "$repo\infra\planning\layer_index.json" -Raw | ConvertFrom-Json).rows | Where-Object { $_.status -eq "indexed" -and $_.kind -eq "zones" } | Select-Object -ExpandProperty plan_id -Unique
$planFlags = "feature.planning.layers feature.planning.coverage-layer " + (($plans | ForEach-Object { "feature.planning.plan.$_" }) -join " ")

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
  Write-Host "Starting planning on :8012 ($($plans -join ', '))"
  $env:FLAGS = $planFlags
  $env:PLANNING_REGISTER_DIR = "$repo\infra\planning"
  $env:PLANNING_WORKER_PYTHON = "$repo\infra\scripts\planning\.venv\Scripts\python.exe"
  $env:CADASTRAL_URL = "http://localhost:8011"
  $pp = Start-Process -FilePath "$repo\services\planning\.venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app", "--port", "8012" `
    -WorkingDirectory "$repo\services\planning" -RedirectStandardOutput "$logs\planning.log" -RedirectStandardError "$logs\planning.err" -WindowStyle Hidden -PassThru
  Set-Content -Encoding ascii "$logs\.lock" $pp.Id  # the service's temp wipe skips its own log folder
} else { Write-Host "planning already running on :8012" }

if (-not (Up "http://localhost:3000")) {
  Write-Host "Starting web on :3000"
  $env:NEXT_PUBLIC_ENABLE_PLANNING_LAYERS = "1"
  $env:NEXT_PUBLIC_PLANNING_PREBUILT_TILES = "1"  # pre-drawn plan tiles from Supabase Storage (#65)
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

Write-Host ""
Write-Host "Ready (plan sheets load on first view: the map shows 'Downloading / extracting ...'):"
Write-Host "  Map (dashboard):  http://localhost:3000/dashboard"
Write-Host "  Planning API:     http://localhost:8012/docs"
Write-Host "  Cadastral API:    http://localhost:8011/docs"
Write-Host "  Logs (temporary): $logs   (stop + delete: builders_all.ps1 -Stop)"
