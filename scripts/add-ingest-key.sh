#!/usr/bin/env bash
# Ajoute INGEST_KEY (clé du collector) à un .env existant, puis redémarre le pont.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Pas de .env : lancez d'abord ./scripts/setup.sh"; exit 1; }

if grep -qE '^INGEST_KEY=.{32,}' .env; then
  echo "INGEST_KEY déjà définie."
else
  KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
        || openssl rand -base64 33 | tr '+/' '-_' | tr -d '=\n')
  if grep -q '^INGEST_KEY=' .env; then
    sed -i "s|^INGEST_KEY=.*|INGEST_KEY=${KEY}|" .env
  else
    printf '\n# Clé du collector du PC Windows (différente de API_KEY)\nINGEST_KEY=%s\n' "$KEY" >> .env
  fi
  echo "✅ INGEST_KEY ajoutée."
fi
# Le collector remplace s4mh comme source.
if grep -q '^VTD_SOURCE=' .env; then sed -i 's|^VTD_SOURCE=.*|VTD_SOURCE=collector|' .env; else echo 'VTD_SOURCE=collector' >> .env; fi

docker compose up -d api
echo
echo "🔑 Clé à copier dans collector/.env sur le PC Windows (INGEST_KEY=…) :"
grep '^INGEST_KEY=' .env | cut -d= -f2-
