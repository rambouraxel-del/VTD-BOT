#!/usr/bin/env bash
# Sauvegarde à chaud : annonces reçues + matchs/ignorés/critères (+ base s4mh si présente).
# Résultat : backups/vtd-AAAA-MM-JJ_HHMM.tar.gz (les 14 dernières sont gardées).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y-%m-%d_%H%M)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

docker compose exec -T api python backup.py > "$TMP/db.tar.gz"
mkdir -p "$TMP/pack"
tar xzf "$TMP/db.tar.gz" -C "$TMP/pack"
cp -r config "$TMP/pack/" 2>/dev/null || true
tar czf "backups/vtd-${STAMP}.tar.gz" -C "$TMP/pack" .
ls -1t backups/vtd-*.tar.gz | tail -n +15 | xargs -r rm --
echo "✅ backups/vtd-${STAMP}.tar.gz"
