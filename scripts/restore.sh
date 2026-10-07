#!/usr/bin/env bash
# Restaure une sauvegarde créée par backup.sh.
#   ./scripts/restore.sh backups/vtd-AAAA-MM-JJ_HHMM.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
FILE=${1:?Usage : ./scripts/restore.sh backups/vtd-....tar.gz}
[ -f "$FILE" ] || { echo "Fichier introuvable : $FILE"; exit 1; }

read -r -p "Remplacer les données actuelles par $FILE ? (oui/non) " answer
[ "$answer" = "oui" ] || { echo "Annulé."; exit 0; }

docker compose --profile s4mh stop s4mh api
# Copie dans les volumes avec l'image du pont (déjà présente, aucun téléchargement).
docker run --rm -i --user 0 --entrypoint sh \
  -v vtd_s4mh_data:/s4mh -v vtd_api_data:/data vtd-api -c '
    set -e
    mkdir -p /tmp/r && tar xzf - -C /tmp/r
    if [ -f /tmp/r/s4mh/vinted.db ]; then rm -f /s4mh/vinted.db-journal; cp /tmp/r/s4mh/vinted.db /s4mh/vinted.db; chown 1000:1000 /s4mh/vinted.db; fi
    if [ -f /tmp/r/data/vtd.db ]; then cp /tmp/r/data/vtd.db /data/vtd.db; chown 1000:1000 /data/vtd.db; fi
  ' < "$FILE"
tar xzf "$FILE" -C . ./config 2>/dev/null || true
docker compose start api
if grep -q '^VTD_SOURCE=s4mh' .env; then docker compose --profile s4mh start s4mh; fi
echo "✅ Restauration terminée."
