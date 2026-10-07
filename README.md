# VTD-BOT — VTD Deals (PWA)

Interface mobile « à la Tinder » pour trier des bonnes affaires Vinted.
Par défaut l'app tourne avec des **données fictives** (aucune connexion Vinted).
Un **pont API** est prêt pour brancher les annonces analysées par
[s4mh/vinted-bot](https://github.com/s4mh/vinted-bot).

**Déploiement sur VPS (Docker + HTTPS) : voir [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).**
**Collecte Vinted depuis le PC Windows : voir [docs/COLLECTOR.md](docs/COLLECTOR.md).**

Architecture actuelle :
`PC Windows (collector) → HTTPS → VPS (API + base + PWA) → iPhone`.
s4mh est conservé mais désactivé (l'endpoint Vinted qu'il utilisait n'existe plus).

## Lancer

```bash
npm install
npm run dev      # mode mock (défaut)
npm run build    # build de production dans dist/
```

Sur iPhone : ouvrir l'URL dans Safari → Partager → « Sur l'écran d'accueil ».

## Écrans

- **Scanner** : swipe droite = Match, gauche = ignorer (+ boutons ❤️ ❌ et ↺ annuler)
- **Mes Matchs** : annonces sauvegardées, bouton « Voir sur Vinted »
- **Critères** : mots-clés, marques, prix max, marge / ROI / score minimum

## Architecture retenue

```
 iPhone (PWA React)                 Pont API (Python, sans dépendance)        s4mh/vinted-bot
┌──────────────────────┐  HTTP    ┌──────────────────────────────┐  lecture  ┌───────────────────┐
│ Écrans               │  JSON    │ server/api.py                │  seule    │ scraper + analyse │
│   ↓                  │ ───────► │  ├ MockSource (mocks)        │ ◄──────── │ data/vinted.db    │
│ useListings          │          │  └ S4mhSource ───────────────┼──────────►│ (seen_listings)   │
│   ↓                  │          │ server/store.py              │           └───────────────────┘
│ ListingService       │          │  statuts + critères (SQLite) │
│  ├ MockListingService│          └──────────────────────────────┘
│  └ ApiListingService │
└──────────────────────┘
```

- Les **écrans ne connaissent que `ListingService`**. Le choix mock / API se fait
  par une variable d'environnement, sans toucher à l'UI.
- s4mh n'expose pas d'API adaptée (pas de statut, pas de détail, pas de CORS) :
  le **pont API** lit sa base **en lecture seule** et ajoute ce qui manque
  (statuts new / matched / ignored / sold, critères).
- s4mh n'est **pas modifié** : on peut le mettre à jour indépendamment.
- Sécurité : toutes les routes (sauf `/api/health`) exigent l'en-tête
  `X-API-Key` (variable d'environnement `API_KEY`, 32 caractères minimum) ;
  CORS limité à `CORS_ORIGINS`. En mode API, l'app demande la clé une fois et
  la garde sur l'appareil.

### Routes du pont API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/health` | public : état du pont, de sa base et de la base s4mh |
| GET | `/api/listings?status=new&keywords=&brands=a,b&maxPrice=&minProfit=&minRoi=&minScore=` | nouvelles annonces filtrées (`status=all` possible) |
| GET | `/api/listings/{id}` | détail d'une annonce |
| PATCH | `/api/listings/{id}` `{"status":"matched"}` | mise à jour du statut |
| GET | `/api/matches` | matchs (et vendus), plus récents en premier |
| GET | `/api/brands` | marques disponibles |
| GET / PUT | `/api/criteria` | critères enregistrés |
| POST | `/api/ingest/listings` | **collector uniquement** (en-tête `X-Ingest-Key`) : ajoute / met à jour des annonces, sans doublon, sans toucher aux statuts |
| POST | `/api/reset` | remet tous les statuts à « new » (tests) |

## Passer du mode mock au backend

**Étape 1 — pont API avec données de test** (aucune donnée réelle) :

```bash
export API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))"); echo $API_KEY
python3 server/api.py                    # terminal 1 : pont sur http://127.0.0.1:8787
cp .env.local.example .env.local         # active VITE_DATA_SOURCE=api
npm run dev                              # terminal 2 : le proxy Vite envoie /api au pont
```

L'app affiche alors l'écran « Clé d'accès requise » : coller la valeur de `API_KEY`.

**Étape 2 — annonces de s4mh** (le pont lit la base de s4mh) :

```bash
API_KEY=... VTD_SOURCE=s4mh S4MH_DB=/chemin/vers/vinted-bot/data/vinted.db python3 server/api.py
# S4MH_ACCEPTED_ONLY=0 pour voir aussi les annonces rejetées par s4mh
```

Pour revenir aux mocks : supprimer `.env.local` (ou `VITE_DATA_SOURCE=mock`).

Variables du pont : `API_KEY` (obligatoire), `CORS_ORIGINS`, `VTD_SOURCE` (mock|s4mh), `S4MH_DB`, `S4MH_ACCEPTED_ONLY`,
`VTD_DB` (base des statuts, défaut `server/data/vtd.db`), `HOST`, `PORT`.

## Tests

```bash
npm run build                          # typecheck + build du front
cd server && python3 -m unittest -v    # tests du pont (ingestion, mock, s4mh, HTTP, clés, CORS, sauvegarde)
cd collector && python3 -m unittest -v # tests du collector (sans Vinted)
docker compose config -q               # valide docker-compose.yml (nécessite un .env)
```

## Fichiers ajoutés / modifiés pour l'intégration

| Fichier | |
|---|---|
| `src/services/ApiListingService.ts` | **ajouté** — implémentation HTTP de `ListingService` |
| `src/services/index.ts` | modifié — choix mock / API via `VITE_DATA_SOURCE` |
| `src/services/ListingService.ts` | modifié — ajout de `getBrands()` |
| `src/services/MockListingService.ts` | modifié — `getBrands()` |
| `src/hooks/useListings.ts`, `src/App.tsx`, `src/screens/CriteriaScreen.tsx` | modifiés — marques chargées via le service, message si serveur injoignable |
| `src/vite-env.d.ts`, `.env.local.example`, `vite.config.ts` | configuration du front (variables, proxy `/api` en dev) |
| `src/screens/ApiKeyScreen.tsx` | **ajouté** — saisie de la clé API (mode API uniquement) |
| `server/api.py`, `server/sources.py`, `server/store.py` | **ajoutés** — pont API |
| `server/mock_listings.json` | **ajouté** — mêmes annonces que `src/data/mockListings.ts` |
| `server/test_api.py` | **ajouté** — tests |
| `docs/INTEGRATION_S4MH.md` | **ajouté** — analyse de s4mh et correspondance des champs |

### Déploiement (Docker)

| Fichier | |
|---|---|
| `docker-compose.yml` | s4mh + pont API + Caddy, volumes persistants, redémarrage auto |
| `.env.example` | variables du déploiement (copier en `.env`, jamais commité) |
| `deploy/s4mh/Dockerfile` | image s4mh construite depuis son dépôt GitHub (version figée) |
| `server/Dockerfile` | image du pont API |
| `deploy/web.Dockerfile`, `deploy/Caddyfile` | PWA compilée en mode API + HTTPS automatique |
| `scripts/` | `setup.sh`, `check.sh`, `backup.sh`, `restore.sh`, `update.sh` |
| `server/backup.py` | sauvegarde à chaud des bases SQLite |
| `docs/DEPLOYMENT.md` | guide de déploiement pas à pas |

### Collector (PC Windows)

| Fichier | |
|---|---|
| `collector/vinted.py` | lecture d'une page de recherche Vinted (repris d'addictcode/vinted-telegram-bot) |
| `collector/collector.py` | filtres, IDs déjà envoyés, envoi HTTPS, pauses en cas de refus, modes one-shot / continu |
| `collector/searches.json` | les 4 recherches (tailles M/L, prix max 50 €) |
| `collector/docker-compose.yml`, `Dockerfile`, `.env.example` | lancement sur Windows ; profil `local` pour un test complet sur le PC |
| `collector/fixtures/catalog_sample.txt` | page d'exemple fictive (tests sans Vinted) |
| `server/scoring.py` | marge / ROI / score des annonces reçues |
| `scripts/add-ingest-key.sh` | ajoute la clé du collector sur un VPS déjà installé |

`src/data/mockListings.ts` est conservé comme mode de développement par défaut.

## Ce qu'il reste à faire pour brancher réellement s4mh

1. Installer et configurer s4mh (niches, `config.yaml`) puis le lancer en
   `--mode test` pour remplir sa base avec des annonces simulées, et vérifier
   l'affichage dans l'app via le pont (`VTD_SOURCE=s4mh`).
2. Décider de lancer s4mh en mode réel — c'est **lui** qui interroge Vinted,
   avec ses propres limites ; le pont et l'app n'y accèdent jamais.
3. ✅ Préparé : déploiement Docker + HTTPS + clé API (voir [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).
4. Statut « vendu » : s4mh ne suit pas les annonces vendues/supprimées. À
   ajouter plus tard (vérification ponctuelle d'un match par s4mh, ou statut
   « vendu » saisi à la main).
5. Optionnel : renvoyer nos matchs / ignorés dans la table `feedback` de s4mh
   pour qu'il apprenne de nos choix ; exposer plusieurs photos (`payload`).

Détail de l'analyse : [docs/INTEGRATION_S4MH.md](docs/INTEGRATION_S4MH.md).
