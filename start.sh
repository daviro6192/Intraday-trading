#!/usr/bin/env bash
# Avvia l'intera piattaforma (backend + frontend) con un solo comando.
# Dopo l'avvio è raggiungibile anche da smartphone/altri dispositivi sulla
# stessa rete Wi-Fi del computer che lo esegue (non solo da localhost).
#
# Uso: ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creo l'ambiente virtuale Python..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installo/aggiorno le dipendenze Python..."
pip install -e ".[dev]" -q

if [ ! -f .env ]; then
  echo "Creo .env con una SECRET_KEY generata automaticamente..."
  cp .env.example .env
  SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
  sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=${SECRET}/" .env && rm -f .env.bak
  echo "Nota: apri .env e imposta ANTHROPIC_API_KEY se vuoi usare la pipeline reale"
  echo "(oppure configurala dopo, dalla pagina Impostazioni della piattaforma)."
fi

if [ ! -d frontend/node_modules ]; then
  echo "Installo le dipendenze del frontend (solo la prima volta, richiede qualche minuto)..."
  (cd frontend && npm install)
fi

echo "Costruisco il frontend..."
(cd frontend && npm run build)

LAN_IP=$(python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
except Exception:
    print("")
finally:
    s.close()
PY
)

echo ""
echo "=================================================================="
echo " Piattaforma pronta. Apri uno di questi indirizzi in un browser:"
echo ""
echo "   da questo computer:      http://localhost:8000"
if [ -n "$LAN_IP" ]; then
  echo "   da smartphone/altro pc:  http://${LAN_IP}:8000   (stessa rete Wi-Fi)"
fi
echo ""
echo " Premi Ctrl+C per fermare il server."
echo "=================================================================="
echo ""

uvicorn api.main:app --host 0.0.0.0 --port 8000
