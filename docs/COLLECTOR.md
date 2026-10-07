# Collector Vinted sur PC Windows

```
PC Windows (Docker Desktop)                     VPS OVH
┌──────────────────────────┐   HTTPS + INGEST_KEY   ┌───────────────────────────┐
│ collector/               │ ─────────────────────► │ Caddy → pont API → base   │ ─► PWA iPhone
│ 4 recherches Vinted      │ POST /api/ingest/...   │ (statuts, critères)       │    (API_KEY)
└──────────────────────────┘                        └───────────────────────────┘
```

Seule la **collecte** tourne sur le PC (là où Vinted répond normalement). Le
VPS garde l'API, la base et la PWA. s4mh est conservé mais désactivé
(l'endpoint `/api/v2/catalog/items` qu'il utilisait renvoie 404).

## Ce que fait le collector

Repris de [addictcode/vinted-telegram-bot](https://github.com/addictcode/vinted-telegram-bot)
(`VintedApiClient` : session anonyme + lecture de la page catalogue Next.js),
réécrit en Python standard, **sans** Telegram, PostgreSQL, proxies, rotation de
User-Agent ni navigateur headless.

1. Pour chaque recherche de `collector/searches.json` : lit la page de résultats
   Vinted (tri « plus récentes »).
2. Garde les annonces qui respectent les critères (taille, prix max, marque,
   mots-clés) et ignore les annonces sponsorisées.
3. Écarte celles déjà envoyées (IDs mémorisés dans le volume Docker `collector_data`).
4. Envoie les nouvelles au VPS en HTTPS (`POST /api/ingest/listings`, en-tête `X-Ingest-Key`).
   Si le VPS est injoignable, elles restent en attente et partent au passage suivant.
5. Recommence toutes les `INTERVAL_SECONDS` (3 min par défaut, 1 min minimum,
   5 à 15 s entre deux recherches).

**Protections Vinted** : en cas de refus (HTTP 401/403/429) ou de page de
challenge, le collector **ne contourne rien** : en one-shot il s'arrête (code 2),
en continu il fait une pause de 15 min, doublée à chaque nouveau refus (max 2 h).

## Format commun envoyé au VPS

```json
{ "listings": [ {
  "id": "4567891234", "title": "Doudoune The North Face Nuptse", "brand": "The North Face",
  "size": "M", "condition": "Très bon état", "price": 45.0, "currency": "EUR",
  "imageUrl": "https://images1.vinted.net/…", "vintedUrl": "https://www.vinted.fr/items/4567891234-…",
  "search": "The North Face vestes/doudounes", "resalePrice": 80, "detectedAt": "2026-…Z"
} ] }
```

Le VPS calcule ensuite marge nette (frais acheteur + port déduits), ROI et score
(`server/scoring.py`), évite les doublons par ID Vinted et ne modifie jamais un
statut matched / ignored existant.

## Les 4 recherches (`collector/searches.json`)

| Recherche | Marques | Mots-clés du titre | Revente estimée |
|---|---|---|---|
| The North Face vestes/doudounes | north face | veste, doudoune, jacket, nuptse, puffer, parka… | 80 € |
| Nike vestes/doudounes vintage | nike | veste, doudoune, jacket, coupe-vent, windbreaker… | 55 € |
| Carhartt vestes | carhartt | veste, jacket, detroit, chore, active… | 70 € |
| Stüssy streetwear | stussy | — | 50 € |

Pour toutes : **tailles M ou L**, **prix ≤ 50 €** (`defaults`). Ces critères sont
vérifiés sur chaque annonce, même si l'URL n'est pas parfaite.

Améliorer une recherche : sur vinted.fr, régler les filtres (catégorie, marque,
tailles, prix max 50 €), copier l'adresse de la page et la coller dans `url`.
Les prix de revente (`resale_price`) sont des estimations à ajuster.
Après modification : `docker compose restart` (pas besoin de reconstruire).

## Installation sur Windows (une seule fois)

1. Installer **Docker Desktop** : https://www.docker.com/products/docker-desktop/
   (accepter WSL 2 si demandé, redémarrer). Dans Docker Desktop → Settings →
   General : cocher **« Start Docker Desktop when you sign in »** pour que le
   collector reparte tout seul au démarrage du PC.
2. Installer **Git** : https://git-scm.com/download/win
3. Ouvrir **PowerShell** et récupérer le projet :
   ```powershell
   cd $HOME
   git clone https://github.com/rambouraxel-del/VTD-BOT.git
   cd VTD-BOT\collector
   copy .env.example .env
   notepad .env
   ```

## Variables de `collector/.env`

| Variable | À mettre |
|---|---|
| `VTD_API_URL` | adresse du VPS, **en https**, sans `/` final : `https://ton-domaine.fr` (le même que l'app) |
| `INGEST_KEY` | **la même valeur** que `INGEST_KEY` dans le `.env` du VPS (sur le VPS : `grep INGEST_KEY .env`) |
| `INTERVAL_SECONDS` | pause entre deux passages (défaut 180, minimum 60) |
| `BACKOFF_MINUTES` / `MAX_BACKOFF_MINUTES` | pause après un refus de Vinted (défaut 15 / 120) |
| `USER_AGENT`, `ACCEPT_LANGUAGE` | laisser par défaut |

`INGEST_KEY` est différente de la clé de l'app (`API_KEY`) : elle permet
seulement d'envoyer des annonces, pas de lire l'app. Ne jamais la commiter
(`.env` est ignoré par Git).

## Tests one-shot

Toutes les commandes se lancent dans `VTD-BOT\collector` (PowerShell).

```powershell
# 1. Sans Vinted ni VPS : page d'exemple fournie, rien n'est envoyé
docker compose run --rm collector --once --dry-run --fixture fixtures/catalog_sample.txt

# 2. Vinted réel, rien n'est envoyé : affiche les annonces retenues
docker compose run --rm collector --once --dry-run

# 3. Vinted réel + envoi au VPS (un seul passage)
docker compose run --rm collector --once
```

La 1re commande construit l'image (une minute). Codes de sortie :
`0` OK · `2` Vinted refuse l'accès · `3` envoi au VPS impossible (URL ou clé) · `4` configuration incomplète.

Ensuite, ouvrir l'app sur l'iPhone : les annonces apparaissent dans le Scanner.
Sur le VPS, `./scripts/check.sh` affiche la date de la dernière réception.

## Mode test complet sur le PC (collector → API de test → frontend)

Sans toucher au VPS : le pont API et la PWA tournent aussi sur le PC.

```powershell
# dans collector\.env : VTD_API_URL=http://api:8787 (INGEST_KEY remplie)
docker compose --profile local up -d --build
docker compose run --rm collector --once --fixture fixtures/catalog_sample.txt   # ou sans --fixture
```

Ouvrir https://localhost:8443 (accepter l'avertissement de certificat local),
coller la clé `LOCAL_API_KEY` de `.env` → les annonces sont dans le Scanner.
Arrêt : `docker compose --profile local down`.

## Mode continu (usage normal)

```powershell
docker compose up -d --build
```

Le collector tourne en arrière-plan et redémarre seul (crash, redémarrage de
Docker Desktop ou du PC). Commandes utiles :

```powershell
docker compose logs -f collector     # suivre l'activité (Ctrl+C pour quitter)
docker compose ps                    # état (healthy = un passage depuis moins de 2 h)
docker compose stop                  # pause
docker compose up -d --build         # après une mise à jour (git pull)
docker compose run --rm collector --once --reset-state   # oublier les IDs déjà envoyés
```

Le PC doit rester allumé (et non en veille) pour que la collecte continue.

## Tests automatiques (développeurs)

```bash
cd collector && python -m unittest -v     # aucun accès à Vinted
```
