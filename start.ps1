# Avvia l'intera piattaforma (backend + frontend) con un solo comando, su Windows/PowerShell.
# Dopo l'avvio è raggiungibile anche da smartphone/altri dispositivi sulla
# stessa rete Wi-Fi del computer che lo esegue (non solo da localhost).
#
# Uso: powershell -ExecutionPolicy Bypass -File .\start.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creo l'ambiente virtuale Python..."
    python -m venv .venv
}

& ".\.venv\Scripts\Activate.ps1"

Write-Host "Installo/aggiorno le dipendenze Python..."
pip install -e ".[dev]" -q

if (-not (Test-Path ".env")) {
    Write-Host "Creo .env con una SECRET_KEY generata automaticamente..."
    Copy-Item ".env.example" ".env"
    $secret = python -c "import secrets; print(secrets.token_hex(32))"
    (Get-Content ".env") -replace '^SECRET_KEY=.*', "SECRET_KEY=$secret" | Set-Content ".env"
    Write-Host "Nota: apri .env e imposta ANTHROPIC_API_KEY se vuoi usare la pipeline reale"
    Write-Host "(oppure configurala dopo, dalla pagina Impostazioni della piattaforma)."
}

if (-not (Test-Path "frontend/node_modules")) {
    Write-Host "Installo le dipendenze del frontend (solo la prima volta, richiede qualche minuto)..."
    Push-Location frontend
    npm install
    Pop-Location
}

Write-Host "Costruisco il frontend..."
Push-Location frontend
npm run build
Pop-Location

$lanIp = (
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254\.' } |
    Select-Object -First 1
).IPAddress

Write-Host ""
Write-Host "=================================================================="
Write-Host " Piattaforma pronta. Apri uno di questi indirizzi in un browser:"
Write-Host ""
Write-Host "   da questo computer:      http://localhost:8000"
if ($lanIp) {
    Write-Host "   da smartphone/altro pc:  http://${lanIp}:8000   (stessa rete Wi-Fi)"
} else {
    Write-Host "   da smartphone/altro pc:  usa 'ipconfig' per trovare il tuo indirizzo IPv4"
}
Write-Host ""
Write-Host " Premi Ctrl+C per fermare il server."
Write-Host "=================================================================="
Write-Host ""

uvicorn api.main:app --host 0.0.0.0 --port 8000
