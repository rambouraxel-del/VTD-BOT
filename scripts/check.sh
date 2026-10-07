#!/usr/bin/env bash
# Vérifie que tout tourne : conteneurs, pont API, base, réception du collector.
set -uo pipefail
cd "$(dirname "$0")/.."
val() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
DOMAIN=$(val DOMAIN); KEY=$(val API_KEY); SOURCE=$(val VTD_SOURCE)
CURL="curl -fsS --max-time 10"
[ "${DOMAIN:-localhost}" = "localhost" ] && CURL="$CURL -k"
ok=0

echo "== Conteneurs"
docker compose ps --format 'table {{.Service}}\t{{.Status}}'

echo; echo "== Santé du pont API (https://${DOMAIN}/api/health)"
if health=$($CURL "https://${DOMAIN}/api/health"); then echo "$health"; else echo "❌ pont API injoignable"; ok=1; fi

echo; echo "== Clé API de l'app"
code=$(curl -s -o /dev/null -w '%{http_code}' -k "https://${DOMAIN}/api/listings")
[ "$code" = "401" ] && echo "✅ requête sans clé refusée (401)" || { echo "❌ attendu 401, reçu $code"; ok=1; }
n=$($CURL -H "X-API-Key: ${KEY}" "https://${DOMAIN}/api/listings?status=all" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))") \
  && echo "✅ avec la clé : ${n} annonce(s)" || { echo "❌ requête avec clé refusée"; ok=1; }

echo; echo "== Ingestion (collector)"
code=$(curl -s -o /dev/null -w '%{http_code}' -k -X POST -H 'Content-Type: application/json' \
       -d '{"listings":[]}' "https://${DOMAIN}/api/ingest/listings")
case "$code" in
  401) echo "✅ route d'ingestion active, refus sans clé (401)" ;;
  503) echo "⚠️ ingestion désactivée : INGEST_KEY absente (./scripts/add-ingest-key.sh)"; ok=1 ;;
  *)   echo "❌ réponse inattendue : $code"; ok=1 ;;
esac
echo "$health" | python3 -c "import json,sys; d=json.load(sys.stdin).get('data',{}); print('   dernière réception :', d.get('last_ingest_at') or 'jamais', '—', d.get('listings', 0), 'annonce(s) en base')" 2>/dev/null

if [ "${SOURCE:-collector}" = "s4mh" ]; then
  echo; echo "== s4mh"
  state=$(docker compose --profile s4mh ps s4mh --format '{{.Status}}')
  echo "s4mh : ${state:-arrêté}"
  case "$state" in Up*) ;; *) ok=1 ;; esac
fi

exit $ok
