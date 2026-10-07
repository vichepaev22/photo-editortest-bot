$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
$TaskPython = Join-Path $TaskRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $TaskRoot
try {
    & $TaskPython -m image_studio stop
    if ($LASTEXITCODE -ne 0) { throw 'Cannot request local bot stop.' }
    $Deadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        $Current = (& $TaskPython -m image_studio status) | ConvertFrom-Json
        if (-not $Current.running) { Write-Output 'Bot stopped.'; exit 0 }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Graceful stop is pending. Check status again; no unrelated process was terminated.'
}
finally { Pop-Location }
