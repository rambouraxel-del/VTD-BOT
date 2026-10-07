import type { Listing, ListingStatus, SearchCriteria } from '../types'
import type { ListingService } from './ListingService'

const KEY_STORAGE = 'vtd.apiKey'

/** Erreur 401 : clé d'accès absente ou invalide. */
export class AuthError extends Error {}

/**
 * La clé API n'est jamais intégrée au build (il est public) : l'utilisateur
 * la saisit une fois dans l'app, elle reste sur son iPhone.
 */
export const apiKeyStore = {
  get: () => {
    try {
      return localStorage.getItem(KEY_STORAGE) ?? ''
    } catch {
      return ''
    }
  },
  set: (key: string) => {
    try {
      localStorage.setItem(KEY_STORAGE, key.trim())
    } catch {
      /* stockage indisponible */
    }
  },
}

/**
 * Implémentation HTTP de ListingService : parle au pont API (server/api.py),
 * qui lui-même lit les annonces analysées par s4mh/vinted-bot.
 */
export class ApiListingService implements ListingService {
  constructor(private baseUrl: string) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response
    try {
      res = await fetch(this.baseUrl + path, {
        ...init,
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKeyStore.get(), ...init?.headers },
      })
    } catch {
      throw new Error(`API injoignable (${this.baseUrl || window.location.origin})`)
    }
    if (res.status === 401) throw new AuthError("Clé d'accès manquante ou invalide")
    if (!res.ok) throw new Error(`API ${res.status} sur ${path}`)
    return (res.status === 204 ? undefined : await res.json()) as T
  }

  getFeed(c: SearchCriteria) {
    const q = new URLSearchParams({
      status: 'new',
      keywords: c.keywords,
      brands: c.brands.join(','),
      maxPrice: String(c.maxPrice),
      minProfit: String(c.minProfit),
      minRoi: String(c.minRoi),
      minScore: String(c.minScore),
    })
    return this.request<Listing[]>(`/api/listings?${q}`)
  }

  getMatches() {
    return this.request<Listing[]>('/api/matches')
  }

  /** Détail d'une annonce (pas encore utilisé par l'UI). */
  getListing(id: string) {
    return this.request<Listing>(`/api/listings/${encodeURIComponent(id)}`)
  }

  async setStatus(id: string, status: ListingStatus) {
    await this.request(`/api/listings/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    })
  }

  getBrands() {
    return this.request<string[]>('/api/brands')
  }

  getCriteria() {
    return this.request<SearchCriteria>('/api/criteria')
  }

  async saveCriteria(criteria: SearchCriteria) {
    await this.request('/api/criteria', { method: 'PUT', body: JSON.stringify(criteria) })
  }

  async reset() {
    await this.request('/api/reset', { method: 'POST' })
  }
}
