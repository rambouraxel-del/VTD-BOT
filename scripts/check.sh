#!/usr/bin/env bash
# Vérifie que tout tourne : conteneurs, pont API, base, s4mh.
set -uo pipefail
cd "$(dirname "$0")/.."
DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)
KEY=$(grep -E '^API_KEY=' .env | cut -d= -f2-)
CURL="curl -fsS --max-time 10"
[ "${DOMAIN:-localhost}" = "localhost" ] && CURL="$CURL -k"
ok=0

echo "== Conteneurs"
docker compose ps --format 'table {{.Service}}\t{{.Status}}'

echo; echo "== Santé du pont API (https://${DOMAIN}/api/health)"
if $CURL "https://${DOMAIN}/api/health"; then echo; else echo "❌ pont API injoignable"; ok=1; fi

echo; echo "== Clé API"
code=$(curl -s -o /dev/null -w '%{http_code}' -k "https://${DOMAIN}/api/listings")
[ "$code" = "401" ] && echo "✅ requête sans clé refusée (401)" || { echo "❌ attendu 401, reçu $code"; ok=1; }
n=$($CURL -H "X-API-Key: ${KEY}" "https://${DOMAIN}/api/listings?status=all" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))") \
  && echo "✅ avec la clé : ${n} annonce(s)" || { echo "❌ requête avec clé refusée"; ok=1; }

echo; echo "== s4mh"
state=$(docker compose ps s4mh --format '{{.State}} {{.Health}}')
echo "s4mh : ${state:-arrêté}"
case "$state" in running*) ;; *) ok=1 ;; esac

exit $ok
