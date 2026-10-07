import type { Listing, ListingStatus, SearchCriteria } from '../types'

/**
 * Contrat unique entre l'UI et la source de données.
 * Aujourd'hui : MockListingService (données locales).
 * Demain : ApiListingService (backend) qui implémente la même interface.
 */
export interface ListingService {
  /** Annonces encore jamais swipées qui respectent les critères. */
  getFeed(criteria: SearchCriteria): Promise<Listing[]>
  /** Annonces sauvegardées (swipe droite). */
  getMatches(): Promise<Listing[]>
  /** Change le statut d'une annonce (match, ignore, vendue, ou retour à "new"). */
  setStatus(id: string, status: ListingStatus): Promise<void>
  getCriteria(): Promise<SearchCriteria>
  saveCriteria(criteria: SearchCriteria): Promise<void>
  /** Remet toutes les annonces à "new" (utile pour les tests). */
  reset(): Promise<void>
}

export const defaultCriteria: SearchCriteria = {
  keywords: '',
  brands: [],
  maxPrice: 1000,
  minProfit: 0,
  minRoi: 0,
  minScore: 0,
}

export function matchesCriteria(l: Listing, c: SearchCriteria): boolean {
  const kw = c.keywords.trim().toLowerCase()
  if (kw && !`${l.title} ${l.brand}`.toLowerCase().includes(kw)) return false
  if (c.brands.length && !c.brands.includes(l.brand)) return false
  return l.price <= c.maxPrice && l.profit >= c.minProfit && l.roi >= c.minRoi && l.score >= c.minScore
}
