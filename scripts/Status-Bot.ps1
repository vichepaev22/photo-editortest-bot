$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $TaskRoot
try { & (Join-Path $TaskRoot '.venv\Scripts\python.exe') -m image_studio status }
finally { Pop-Location }
