# Run a long planning job detached (WMI Win32_Process.Create), so it survives the terminal.
# The log goes to %TEMP%\qnit_planning\logs\<name>\run.log while it runs; the job locks that
# folder with its own PID (QNIT_JOB_DIR), so the planning service's temp wipe skips it.
# Read the log when the job ends, report it, then delete the folder (logs are temporary).
#
#   powershell -File infra\scripts\dev\run_detached.ps1 -Name build_index -Python <py> -ScriptArgs "<script> <args>"
param(
  [Parameter(Mandatory = $true)][string]$Name,
  [Parameter(Mandatory = $true)][string]$Python,
  [Parameter(Mandatory = $true)][string]$ScriptArgs,
  [string]$EnvVars = ""
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$dir = Join-Path $env:TEMP "qnit_planning\logs\$Name"
New-Item -ItemType Directory -Force $dir | Out-Null
$log = Join-Path $dir "run.log"
$setEnv = "set QNIT_JOB_DIR=$dir&& set PYTHONUNBUFFERED=1&& "
foreach ($kv in ($EnvVars -split ";" | Where-Object { $_ })) { $setEnv += "set $kv&& " }
$cmd = "cmd.exe /c `"$setEnv`"$Python`" -X faulthandler $ScriptArgs > `"$log`" 2>&1 && echo EXIT 0 >> `"$log`" || echo EXIT 1 >> `"$log`"`""
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $repo }
"started $Name (pid $($r.ProcessId)); log $log"
