# VTD-BOT — VTD Deals (PWA)

Interface mobile « à la Tinder » pour trier des bonnes affaires Vinted.
Par défaut l'app tourne avec des **données fictives** (aucune connexion Vinted).
Un **pont API** est prêt pour brancher les annonces analysées par
[s4mh/vinted-bot](https://github.com/s4mh/vinted-bot).

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

### Routes du pont API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/health` | état + source (`mock` ou `s4mh`) |
| GET | `/api/listings?status=new&keywords=&brands=a,b&maxPrice=&minProfit=&minRoi=&minScore=` | nouvelles annonces filtrées (`status=all` possible) |
| GET | `/api/listings/{id}` | détail d'une annonce |
| PATCH | `/api/listings/{id}` `{"status":"matched"}` | mise à jour du statut |
| GET | `/api/matches` | matchs (et vendus), plus récents en premier |
| GET | `/api/brands` | marques disponibles |
| GET / PUT | `/api/criteria` | critères enregistrés |
| POST | `/api/reset` | remet tous les statuts à « new » (tests) |

## Passer du mode mock au backend

**Étape 1 — pont API avec données de test** (aucune donnée réelle) :

```bash
python3 server/api.py                    # terminal 1 : pont sur http://127.0.0.1:8787
cp .env.example .env.local               # active VITE_DATA_SOURCE=api
npm run dev                              # terminal 2 : le proxy Vite envoie /api au pont
```

**Étape 2 — annonces de s4mh** (le pont lit la base de s4mh) :

```bash
VTD_SOURCE=s4mh S4MH_DB=/chemin/vers/vinted-bot/data/vinted.db python3 server/api.py
# S4MH_ACCEPTED_ONLY=0 pour voir aussi les annonces rejetées par s4mh
```

Pour revenir aux mocks : supprimer `.env.local` (ou `VITE_DATA_SOURCE=mock`).

Variables du pont : `VTD_SOURCE` (mock|s4mh), `S4MH_DB`, `S4MH_ACCEPTED_ONLY`,
`VTD_DB` (base des statuts, défaut `server/data/vtd.db`), `HOST`, `PORT`, `CORS_ORIGIN`.

## Tests

```bash
npm run build                          # typecheck + build du front
cd server && python3 -m unittest -v    # tests du pont (mock, correspondance s4mh, HTTP)
```

## Fichiers ajoutés / modifiés pour l'intégration

| Fichier | |
|---|---|
| `src/services/ApiListingService.ts` | **ajouté** — implémentation HTTP de `ListingService` |
| `src/services/index.ts` | modifié — choix mock / API via `VITE_DATA_SOURCE` |
| `src/services/ListingService.ts` | modifié — ajout de `getBrands()` |
| `src/services/MockListingService.ts` | modifié — `getBrands()` |
| `src/hooks/useListings.ts`, `src/App.tsx`, `src/screens/CriteriaScreen.tsx` | modifiés — marques chargées via le service, message si serveur injoignable |
| `src/vite-env.d.ts`, `.env.example`, `vite.config.ts` | configuration (variables, proxy `/api` en dev) |
| `server/api.py`, `server/sources.py`, `server/store.py` | **ajoutés** — pont API |
| `server/mock_listings.json` | **ajouté** — mêmes annonces que `src/data/mockListings.ts` |
| `server/test_api.py` | **ajouté** — tests |
| `docs/INTEGRATION_S4MH.md` | **ajouté** — analyse de s4mh et correspondance des champs |

`src/data/mockListings.ts` est conservé comme mode de développement par défaut.

## Ce qu'il reste à faire pour brancher réellement s4mh

1. Installer et configurer s4mh (niches, `config.yaml`) puis le lancer en
   `--mode test` pour remplir sa base avec des annonces simulées, et vérifier
   l'affichage dans l'app via le pont (`VTD_SOURCE=s4mh`).
2. Décider de lancer s4mh en mode réel — c'est **lui** qui interroge Vinted,
   avec ses propres limites ; le pont et l'app n'y accèdent jamais.
3. Héberger le pont (même machine que s4mh) derrière **HTTPS** avec une
   **authentification** (ex. clé d'API), car l'app installée sur iPhone ne peut
   pas joindre `127.0.0.1` d'un PC, et une page HTTPS ne peut pas appeler du HTTP.
   Puis builder le front avec `VITE_DATA_SOURCE=api` et `VITE_API_URL=https://…`.
4. Statut « vendu » : s4mh ne suit pas les annonces vendues/supprimées. À
   ajouter plus tard (vérification ponctuelle d'un match par s4mh, ou statut
   « vendu » saisi à la main).
5. Optionnel : renvoyer nos matchs / ignorés dans la table `feedback` de s4mh
   pour qu'il apprenne de nos choix ; exposer plusieurs photos (`payload`).

Détail de l'analyse : [docs/INTEGRATION_S4MH.md](docs/INTEGRATION_S4MH.md).
