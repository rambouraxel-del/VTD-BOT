# VTD-BOT — VTD Deals (PWA)

Interface mobile « à la Tinder » pour trier des bonnes affaires Vinted.
**Données 100 % fictives pour l'instant** (aucune connexion Vinted, aucun backend).

## Lancer

```bash
npm install
npm run dev      # dev (accessible sur le réseau local pour tester sur iPhone)
npm run build    # build de production dans dist/
npm run preview  # tester le build (PWA installable)
```

Sur iPhone : ouvrir l'URL dans Safari → Partager → « Sur l'écran d'accueil ».

## Écrans

- **Scanner** : swipe droite = Match, gauche = ignorer (+ boutons ❤️ ❌ et ↺ annuler)
- **Mes Matchs** : annonces sauvegardées, bouton « Voir sur Vinted »
- **Critères** : mots-clés, marques, prix max, marge / ROI / score minimum

## Brancher le futur backend

Toute l'UI passe par l'interface `ListingService` (`src/services/ListingService.ts`).

1. Créer `src/services/ApiListingService.ts` qui implémente `ListingService` (appels `fetch`).
2. Dans `src/services/index.ts`, remplacer `new MockListingService()` par `new ApiListingService()`.

Aucun composant UI à modifier.

## Structure

```
src/
  types.ts                     type Listing, SearchCriteria
  data/mockListings.ts         12 annonces fictives
  services/                    ListingService (contrat) + MockListingService (localStorage)
  hooks/useListings.ts         état de l'app
  components/                  SwipeDeck, SwipeCard, TabBar…
  screens/                     Feed, Matches, Criteria
public/                        manifest, service worker, icônes
```
