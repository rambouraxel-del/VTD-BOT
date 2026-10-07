export type ListingStatus = 'new' | 'matched' | 'ignored' | 'sold'

export type Listing = {
  id: string
  title: string
  brand: string
  size: string
  condition: string
  price: number
  resalePrice: number
  profit: number
  roi: number
  score: number
  imageUrl: string
  vintedUrl: string
  status: ListingStatus
  /** Infos / alertes courtes affichées sur la carte (2-3 max). */
  tags?: string[]
}

/** Critères de recherche / filtres du feed. */
export type SearchCriteria = {
  keywords: string
  brands: string[]
  maxPrice: number
  minProfit: number
  minRoi: number
  minScore: number
}
