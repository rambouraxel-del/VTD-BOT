# Analyse de s4mh/vinted-bot et correspondance avec VTD Deals

Analyse du dépôt https://github.com/s4mh/vinted-bot (Python 3.11+, licence MIT).
Aucune requête vers Vinted n'a été faite pendant cette analyse : les essais ont
utilisé le mode test de s4mh (`--mode test`, annonces simulées, URLs `vinted.invalid`).

## 1. Ce que fait s4mh

| Sujet | Fonctionnement dans s4mh |
|---|---|
| **Architecture** | Bot Python asynchrone : `scheduler` → `pipeline` (scraper → anti-doublon → analyse → BDD → Discord). Dashboard web FastAPI local. Discord pour les alertes et l'achat. |
| **Récupération** | `app/scraper/vinted.py` appelle l'API JSON interne de Vinted (recherche par « niche »), avec protection anti-blocage (`politeness.py` : cadence, budgets, disjoncteur, pause nocturne). Un simulateur (`simulator/`) remplace le scraper en mode test. |
| **Format** | Dataclass `Listing` (`app/models.py`) : id, title, price, url, description, brand, category, size, condition, color, photos[], seller{login, rating, reviews_count…}, published_at, total_price, favourites, views. Chaque annonce produit une `Analysis` : score, défauts, rentabilité, accepted, rejection_reasons, notes. |
| **Stockage / BDD** | SQLAlchemy async, **SQLite par défaut** (`data/vinted.db`, ou PostgreSQL/MySQL via `DATABASE_URL`). Table principale **`seen_listings`** : une ligne par annonce analysée, avec les champs plats + `payload` JSON (analyse complète). Autres tables : `purchase_attempts`, `feedback`, `cycle_stats`, `kv_state`. |
| **Scoring** | `app/analyzer/scoring.py` : score sur 100 = somme pondérée de 6 composantes (prix, marque, état, marge, vendeur, fraîcheur) + bonus/malus, moins une pénalité de risque. Seuil par niche → `accepted`. |
| **Marge / ROI** | `app/profitability/calculator.py` : coût total = prix + protection acheteur + port ; revente estimée (prix fixe de la niche, multiplicateur, prix de marché observé, table par marque, ou multiplicateur par défaut) ; **marge nette** = revente − frais de revente − coût total ; **ROI = marge / coût total**. |
| **Doublons** | Contrainte d'unicité sur `seen_listings.vinted_id` + filtre `filter_new_ids` avant analyse : une annonce n'est traitée qu'une fois, même après redémarrage. |
| **Supprimées / vendues** | **Pas de suivi.** s4mh ne revérifie une annonce qu'au moment d'un achat (`security/guards.py` : erreur 404 → « supprimée ou vendue »). `purge_old` supprime les annonces non retenues après N jours. |
| **API existante** | Dashboard FastAPI (`app/dashboard/server.py`), sur `127.0.0.1:3000`, **sans authentification ni CORS**. Route utile : `GET /api/listings?accepted=&min_score=&search=&niche=&limit=&offset=`. Le reste sert à piloter le bot (start/stop, niches, config, logs, stats). **Aucune route de détail d'annonce ni de statut.** |
| **Retours utilisateur** | Boutons Discord ⭐ Favori / ❌ Ignorer → table `feedback` (utilisée pour ajuster les seuils). Proche de nos matchs/ignorés, mais lié à un utilisateur Discord. |

## 2. Correspondance avec notre type `Listing`

Source : colonnes de la table `seen_listings` (s4mh) → `Listing` (`src/types.ts`).
Le code de correspondance est dans `server/sources.py` (`s4mh_row_to_listing`).

| Champ VTD Deals | Source s4mh | Traitement |
|---|---|---|
| `id` | `vinted_id` | direct (converti en texte) |
| `title` | `title` | direct |
| `brand` | `brand` | direct |
| `size` | `size` | direct |
| `condition` | `condition` | direct (texte Vinted, ex. « Très bon état ») |
| `price` | `price` | direct (prix affiché, **hors** frais acheteur) |
| `resalePrice` | `resale_price` | direct (estimation s4mh) |
| `profit` | `profit` | direct — ⚠️ marge **nette** (frais et port déduits), donc ≠ `resalePrice − price` |
| `roi` | `roi_percent` | arrondi — ⚠️ calculé sur le **coût total**, pas sur le prix seul |
| `score` | `score` | arrondi, borné 0–100 |
| `imageUrl` | `photo_url` | direct (1re photo seulement ; les autres sont dans `payload`) |
| `vintedUrl` | `url` | direct |
| `tags` | `defect_level`, `defect_words`, `published_at`, `seller_rating`, `seller_reviews`, `risk` | **calculé** : « ⚠️ Défaut possible : tache », « ⚡ Publiée il y a 4 min », « ⭐ Vendeur 4.9 (120 avis) », « 🆕 Vendeur récent », « ⚠️ Risque élevé » (3 max) |
| `status` | — | **absent dans s4mh** : géré par notre pont API (`server/store.py`) |

Champs s4mh disponibles mais non utilisés pour l'instant : niche, catégorie,
description, vendeur (login), risque détaillé, raisons de rejet, latence de
détection, détail des composantes du score (dans `payload`).
