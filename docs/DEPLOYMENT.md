# Déploiement sur un VPS Linux (24/7)

```
PC Windows : collector ──HTTPS (INGEST_KEY)──► Caddy ──► pont API + base ──► PWA iPhone (API_KEY)
                                              conteneur web   conteneur api
```

La **collecte Vinted tourne sur le PC Windows** (voir [COLLECTOR.md](COLLECTOR.md)).
Le VPS ne contacte jamais Vinted : il reçoit les annonces, les stocke et sert l'app.
Tout se lance avec **une seule commande** (`docker compose up -d --build`).

| Conteneur | Rôle | Exposé sur Internet |
|---|---|---|
| `api` | pont API : reçoit les annonces du collector, stocke matchs / ignorés / critères | non (uniquement via Caddy) |
| `web` | Caddy : HTTPS automatique, sert la PWA, relaie `/api` | oui, ports 80 et 443 |
| `s4mh` | **désactivé** (ancienne source, endpoint Vinted retiré) — profil `s4mh` | non |

## 1. Prérequis du VPS

- **Ubuntu 24.04 LTS** (ou 22.04 / Debian 12), accès SSH root ou sudo.
- **Minimum : 1 vCPU, 1 Go de RAM + 2 Go de swap, 20 Go de disque.**
  Confortable : 2 vCPU, 2 Go de RAM. En fonctionnement, l'ensemble utilise
  moins de 150 Mo de RAM (mesuré : ~90 Mo) ; la mémoire sert surtout pendant la reconstruction des images.
- Un **nom de domaine** (ou sous-domaine) dont l'enregistrement DNS **A** pointe
  vers l'IP du VPS — nécessaire pour le certificat HTTPS.
- Ports **80** et **443** ouverts.

## 2. Installation (une seule fois)

Connecté au VPS en SSH :

```bash
# Mises à jour + pare-feu (SSH, HTTP, HTTPS uniquement)
sudo apt update && sudo apt upgrade -y
sudo apt install -y git ufw
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp
sudo ufw --force enable

# Swap de 2 Go (utile sur 1 Go de RAM)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker (script officiel) + droits pour l'utilisateur courant
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker

# Récupération du projet
git clone https://github.com/rambouraxel-del/VTD-BOT.git ~/vtd
cd ~/vtd
```

Docker redémarre automatiquement au boot du VPS, et les conteneurs aussi
(`restart: unless-stopped`).

## 3. Variables à remplir

```bash
./scripts/setup.sh
```

Le script crée `.env` (jamais commité) à partir de `.env.example`, **génère
une clé API aléatoire** et demande le domaine. Il affiche la clé : **notez-la**
(gestionnaire de mots de passe), elle servira dans l'app.

Variables principales de `.env` :

| Variable | Rôle | Valeur par défaut |
|---|---|---|
| `DOMAIN` | domaine du VPS (certificat HTTPS automatique) | `localhost` (test) |
| `API_KEY` | clé secrète de l'app, **obligatoire**, ≥ 32 caractères | générée par `setup.sh` |
| `INGEST_KEY` | clé du collector (envoi des annonces), **différente** de `API_KEY` | générée par `setup.sh` |
| `CORS_ORIGINS` | autres sites autorisés à appeler l'API (vide = même domaine) | vide |
| `VTD_SOURCE` | `collector` (annonces du PC), `mock` (12 annonces fictives) ou `s4mh` (désactivé) | `collector` |
| `S4MH_*` | réglages de s4mh, utilisés seulement avec `VTD_SOURCE=s4mh` | — |

Pour modifier : `nano .env` puis `docker compose up -d`.

## 4. Démarrage

```bash
docker compose up -d --build     # 1er lancement : quelques minutes (construction des images)
./scripts/check.sh               # vérifie que tout fonctionne
```

`check.sh` contrôle : les conteneurs, `https://DOMAIN/api/health` (pont API,
base, nombre d'annonces reçues, date de la dernière réception du collector),
le refus d'une requête sans clé (401), l'accès avec la clé et la route
d'ingestion du collector.

### VPS déjà installé avant le collector

```bash
cd ~/vtd
git pull
./scripts/add-ingest-key.sh        # crée INGEST_KEY, passe VTD_SOURCE=collector, redémarre le pont
docker compose up -d --build       # reconstruit le pont (nouvelle route d'ingestion)
docker compose --profile s4mh stop s4mh 2>/dev/null; docker compose --profile s4mh rm -f s4mh 2>/dev/null
./scripts/check.sh
```

La clé affichée par `add-ingest-key.sh` va dans `collector/.env` sur le PC.

Autres commandes utiles :

```bash
docker compose ps                    # état (healthy = OK)
docker compose logs -f s4mh          # journaux de s4mh (Ctrl+C pour quitter)
docker compose logs -f api web       # journaux du pont et de Caddy
docker compose restart s4mh          # redémarrer un service
```


## 5. Arrêt

```bash
docker compose stop      # arrêt (redémarre avec docker compose start)
docker compose down      # arrêt + suppression des conteneurs — les DONNÉES sont conservées
```

⚠️ Ne jamais utiliser `docker compose down -v` : `-v` **supprime les volumes**,
donc la base s4mh, les matchs, les ignorés et les critères.

## 6. Mise à jour depuis GitHub

```bash
./scripts/update.sh
```

Le script fait une sauvegarde, `git pull`, reconstruit les images
(`docker compose up -d --build`), nettoie les anciennes images puis lance `check.sh`.
Les données sont dans des **volumes Docker nommés** (`vtd_s4mh_data`,
`vtd_api_data`, `vtd_caddy_data`) qui ne sont jamais touchés par une
reconstruction.

Mettre à jour s4mh : changer `S4MH_REF` dans `.env` (commit de
https://github.com/s4mh/vinted-bot), puis `docker compose up -d --build s4mh`.

## 7. Sauvegarde et restauration

```bash
./scripts/backup.sh                                   # → backups/vtd-AAAA-MM-JJ_HHMM.tar.gz
./scripts/restore.sh backups/vtd-AAAA-MM-JJ_HHMM.tar.gz
```

- La sauvegarde est faite **à chaud** (copie cohérente même si s4mh écrit).
  Elle contient les annonces reçues, les matchs / ignorés / critères (et la base s4mh si elle existe).
  Les 14 dernières sont conservées.
- Sauvegarde automatique chaque nuit à 4 h :
  ```bash
  (crontab -l 2>/dev/null; echo "0 4 * * * cd $HOME/vtd && ./scripts/backup.sh >> backups/backup.log 2>&1") | crontab -
  ```
- Copier les sauvegardes hors du VPS (depuis votre ordinateur) :
  `scp utilisateur@IP_DU_VPS:vtd/backups/*.tar.gz .`
- `.env` n'est pas dans les sauvegardes (il contient la clé) : gardez la clé
  dans un gestionnaire de mots de passe.

## 8. Connexion du frontend (iPhone)

La PWA est servie par Caddy sur le même domaine que l'API, déjà compilée en
mode API (`VITE_DATA_SOURCE=api`, `VITE_API_URL` vide = même domaine).

1. Sur l'iPhone, ouvrir **https://DOMAIN** dans Safari.
2. Coller la **clé API** (affichée par `setup.sh`, ou `grep API_KEY .env`) → « Se connecter ».
   Elle est enregistrée sur l'iPhone uniquement, jamais dans le code.
3. Partager → « Sur l'écran d'accueil ».

La version GitHub Pages reste en mode mock (démo). Pour qu'une PWA hébergée
ailleurs appelle l'API : la compiler avec `VITE_DATA_SOURCE=api` et
`VITE_API_URL=https://DOMAIN`, et ajouter son adresse dans `CORS_ORIGINS`.

Changer la clé : générer une nouvelle valeur dans `.env`, `docker compose up -d api`,
puis la saisir à nouveau sur l'iPhone (l'écran de clé réapparaît tout seul).

## 9. Collecte des vraies annonces

La collecte se fait depuis le **PC Windows** : voir [COLLECTOR.md](COLLECTOR.md).

### Retour arrière vers s4mh (désactivé)

s4mh ne fonctionne plus (Vinted a retiré l'endpoint `/api/v2/catalog/items`).
Il reste dans le projet pour pouvoir revenir en arrière si besoin :
`VTD_SOURCE=s4mh` dans `.env`, puis `docker compose --profile s4mh up -d --build`.
Dashboard s4mh (tunnel SSH) : `ssh -L 3000:127.0.0.1:3000 utilisateur@IP_DU_VPS`.
Réglages s4mh (anciennes instructions) :

1. Récupérer et ajuster la configuration de s4mh (niches, seuils, cadence) :
   ```bash
   docker compose run --rm --no-deps --entrypoint cat s4mh config.example.yaml > config/s4mh/config.yaml
   nano config/s4mh/config.yaml
   ```
2. Dans `.env` : `S4MH_ARGS=--mode test --no-discord` (simulation) ou `--mode live`.

## 10. Dépannage

| Symptôme | Piste |
|---|---|
| `API_KEY manquante` au lancement | lancer `./scripts/setup.sh` ou remplir `API_KEY` dans `.env` |
| Pas de certificat HTTPS | DNS du domaine vers l'IP du VPS ? ports 80/443 ouverts ? `docker compose logs web` |
| Aucune annonce dans l'app | collector lancé sur le PC ? `./scripts/check.sh` (dernière réception), `docker compose logs api` |
| Collector : « HTTP 401 » | `INGEST_KEY` différente entre le PC et le VPS |
| Collector : « HTTP 503 » | `INGEST_KEY` absente sur le VPS : `./scripts/add-ingest-key.sh` |
| L'app redemande la clé | clé changée ou mal copiée : la recoller |
| Disque plein | `docker system prune` (ne touche pas aux volumes) |
