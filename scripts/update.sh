#!/usr/bin/env bash
# Mise à jour depuis GitHub : sauvegarde, récupération du code, reconstruction.
# Les données (volumes vtd_*) sont conservées.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/backup.sh || echo "⚠️ sauvegarde impossible (premier lancement ?), on continue"
git pull --ff-only
docker compose up -d --build
docker image prune -f > /dev/null
echo "✅ Mise à jour terminée."
./scripts/check.sh || true
