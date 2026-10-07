import { mockListings } from '../data/mockListings'
import type { Listing, ListingStatus, SearchCriteria } from '../types'
import { defaultCriteria, matchesCriteria, type ListingService } from './ListingService'

const STATUS_KEY = 'vtd.statuses'
const CRITERIA_KEY = 'vtd.criteria'

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* stockage indisponible : on ignore */
  }
}

/** Implémentation locale : mocks + statuts persistés dans le localStorage. */
export class MockListingService implements ListingService {
  private withStatus(): Listing[] {
    const statuses = read<Record<string, ListingStatus>>(STATUS_KEY, {})
    return mockListings.map((l) => ({ ...l, status: statuses[l.id] ?? l.status }))
  }

  async getFeed(criteria: SearchCriteria) {
    return this.withStatus()
      .filter((l) => l.status === 'new' && matchesCriteria(l, criteria))
      .sort((a, b) => b.score - a.score)
  }

  async getMatches() {
    const order = read<string[]>(STATUS_KEY + '.order', [])
    return this.withStatus()
      .filter((l) => l.status === 'matched' || l.status === 'sold')
      .sort((a, b) => order.indexOf(b.id) - order.indexOf(a.id))
  }

  async setStatus(id: string, status: ListingStatus) {
    const statuses = read<Record<string, ListingStatus>>(STATUS_KEY, {})
    if (status === 'new') delete statuses[id]
    else statuses[id] = status
    write(STATUS_KEY, statuses)
    // Garde l'ordre d'ajout des matchs (le plus récent en premier).
    const order = read<string[]>(STATUS_KEY + '.order', []).filter((x) => x !== id)
    if (status === 'matched') order.push(id)
    write(STATUS_KEY + '.order', order)
  }

  async getCriteria() {
    return { ...defaultCriteria, ...read<Partial<SearchCriteria>>(CRITERIA_KEY, {}) }
  }

  async saveCriteria(criteria: SearchCriteria) {
    write(CRITERIA_KEY, criteria)
  }

  async reset() {
    write(STATUS_KEY, {})
    write(STATUS_KEY + '.order', [])
  }

  async getBrands() {
    return [...new Set(mockListings.map((l) => l.brand))].sort()
  }
}
