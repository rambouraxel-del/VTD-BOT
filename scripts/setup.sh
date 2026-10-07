#!/usr/bin/env bash
# Première installation : crée .env avec une clé API aléatoire.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env existe déjà : rien à faire (supprimez-le pour recommencer)."
  exit 0
fi

gen() {
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
    || openssl rand -base64 33 | tr '+/' '-_' | tr -d '=\n'
}
KEY=$(gen)
INGEST=$(gen)
sed -e "s|^API_KEY=.*|API_KEY=${KEY}|" -e "s|^INGEST_KEY=.*|INGEST_KEY=${INGEST}|" .env.example > .env
chmod 600 .env

read -r -p "Nom de domaine (laisser vide = localhost pour tester) : " DOMAIN || true
if [ -n "${DOMAIN:-}" ]; then
  sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env
fi

echo
echo "✅ .env créé."
echo "🔑 Clé API (à coller dans l'app sur l'iPhone, à garder secrète) :"
echo "   ${KEY}"
echo "   (elle reste lisible plus tard avec : grep API_KEY .env)"
echo "🔑 Clé d'ingestion (à copier dans collector/.env sur le PC Windows) :"
echo "   ${INGEST}"
echo "   (grep INGEST_KEY .env)"
