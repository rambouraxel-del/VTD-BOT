import { ApiListingService, apiKeyStore, AuthError } from './ApiListingService'
import type { ListingService } from './ListingService'
import { MockListingService } from './MockListingService'

/**
 * Point de branchement unique de la source de données.
 *
 * VITE_DATA_SOURCE=mock (défaut) → mocks locaux (src/data/mockListings.ts)
 * VITE_DATA_SOURCE=api           → pont API (server/api.py) à VITE_API_URL
 *
 * Aucun composant UI ne dépend de ce choix.
 */
const source = import.meta.env.VITE_DATA_SOURCE ?? 'mock'

export const listingService: ListingService =
  source === 'api' ? new ApiListingService(import.meta.env.VITE_API_URL ?? '') : new MockListingService()

export * from './ListingService'
export { apiKeyStore, AuthError }
