import type { ListingService } from './ListingService'
import { MockListingService } from './MockListingService'

/**
 * Point de branchement unique de la source de données.
 * Pour passer au backend : créer ApiListingService (même interface)
 * et remplacer la ligne ci-dessous. Aucun composant UI à modifier.
 */
export const listingService: ListingService = new MockListingService()

export const availableBrands: string[] = MockListingService.brands()

export * from './ListingService'
